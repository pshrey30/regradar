import { createContext, useEffect, useState, type ReactNode } from 'react'

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

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    status: 'loading',
    role: null,
    organizationId: null,
    displayName: null,
  })

  useEffect(() => {
    // Any 401 from anywhere in the app (not just this one /v1/me call)
    // drops straight to "unauthenticated" — the ticket's own requirement
    // that a 401 on any API call redirects to login.
    setOnUnauthorized(() => setState((s) => ({ ...s, status: 'unauthenticated' })))

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
        // ApiError(401) already flipped state via setOnUnauthorized above;
        // anything else (network down, etc.) is treated the same way —
        // there's no partial-auth state this app needs to distinguish.
        if (!(err instanceof ApiError) || err.status !== 401) {
          setState((s) => ({ ...s, status: 'unauthenticated' }))
        }
      })
  }, [])

  return <AuthContext.Provider value={state}>{children}</AuthContext.Provider>
}
