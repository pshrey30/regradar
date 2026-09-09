import { createContext, useCallback, useEffect, useState, type ReactNode } from 'react'

import { ApiError, apiFetch, setOnUnauthorized } from '../lib/api'

export type Role = 'admin' | 'analyst' | 'executive' | 'legal_counsel' | 'eng_lead'

interface MeResponse {
  role: Role
  organization_id: string | null
  display_name: string
}

export interface AuthState {
  status: 'loading' | 'authenticated' | 'unauthenticated'
  role: Role | null
  organizationId: string | null
  displayName: string | null
}

export const AuthContext = createContext<AuthState | null>(null)

const _INITIAL_STATE: AuthState = {
  status: 'loading',
  role: null,
  organizationId: null,
  displayName: null,
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(_INITIAL_STATE)

  const checkSession = useCallback(() => {
    apiFetch<MeResponse>('/v1/me')
      .then((me) =>
        setState({
          status: 'authenticated',
          role: me.role,
          organizationId: me.organization_id,
          displayName: me.display_name,
        }),
      )
      .catch((err) => {
        // ApiError(401) already flipped state via setOnUnauthorized below;
        // anything else (network down, etc.) is treated the same way —
        // there's no partial-auth state this app needs to distinguish.
        if (!(err instanceof ApiError) || err.status !== 401) {
          setState((s) => ({ ...s, status: 'unauthenticated' }))
        }
      })
  }, [])

  useEffect(() => {
    // Any 401 from anywhere in the app (not just this one /v1/me call)
    // drops straight to "unauthenticated" — the ticket's own requirement
    // that a 401 on any API call redirects to login.
    setOnUnauthorized(() => setState((s) => ({ ...s, status: 'unauthenticated' })))

    checkSession()

    // The bfcache problem: navigating back to a protected page after
    // signing out can restore the *entire previous render* — including
    // this component's in-memory "authenticated" state — straight from
    // the browser's back-forward cache, without this effect (or any
    // network request) re-running at all. The page would briefly show
    // stale authenticated content for a signed-out user. `pageshow`'s
    // `persisted` flag is the standard signal a bfcache restore just
    // happened; re-running the session check here re-validates against
    // the server and flips back to `unauthenticated` (via the 401 above)
    // if the session was actually revoked, redirecting the page for
    // every protected route uniformly (ProtectedRoute reads this same
    // state) instead of needing a per-page fix.
    function handlePageShow(event: PageTransitionEvent) {
      if (event.persisted) {
        setState((s) => ({ ...s, status: 'loading' }))
        checkSession()
      }
    }
    window.addEventListener('pageshow', handlePageShow)
    return () => window.removeEventListener('pageshow', handlePageShow)
  }, [checkSession])

  return <AuthContext.Provider value={state}>{children}</AuthContext.Provider>
}
