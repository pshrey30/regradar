import { useState, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { canSeeNavItem, useAuth } from '../auth/useAuth'
import { API_BASE_URL } from '../lib/api'
import { Button } from './Button'

// `path` is only set for a nav item whose screen actually exists yet
// (FE-06 through FE-09's screens aren't built — those stay inert text,
// same as every nav item was before FE-05 added the first real
// destination to click *to* from elsewhere in the nav).
const _NAV_LINKS: { key: Parameters<typeof canSeeNavItem>[1]; label: string; path?: string }[] = [
  { key: 'filings', label: 'Filings', path: '/filings' },
  { key: 'search', label: 'Ask RegRadar', path: '/search' },
  { key: 'webhooks', label: 'Webhooks', path: '/webhooks' },
  { key: 'api_keys', label: 'API Keys', path: '/api-keys' },
  { key: 'metrics', label: 'Metrics & Cost', path: '/metrics' },
  { key: 'source_config', label: 'Source Configuration' },
]

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const { role } = useAuth()
  const location = useLocation()

  return (
    <ul className="flex flex-1 flex-col gap-1">
      {_NAV_LINKS.filter((item) => canSeeNavItem(role, item.key)).map((item) => {
        const isActive = item.path === location.pathname
        if (!item.path) {
          return (
            <li key={item.key}>
              <span className="block cursor-default rounded-md px-3 py-2 text-sm text-slate-400">
                {item.label}
              </span>
            </li>
          )
        }
        return (
          <li key={item.key}>
            <Link
              to={item.path}
              onClick={onNavigate}
              className={[
                'block rounded-md px-3 py-2 text-sm transition-colors',
                isActive
                  ? 'bg-primary-50 font-medium text-primary-700'
                  : 'text-slate-600 hover:bg-slate-100',
              ].join(' ')}
            >
              {item.label}
            </Link>
          </li>
        )
      })}
    </ul>
  )
}

function SignOutForm() {
  const { displayName } = useAuth()
  return (
    <div className="border-t border-slate-200 pt-4">
      <p className="mb-2 truncate text-xs text-slate-500">{displayName}</p>
      <form action={`${API_BASE_URL}/v1/auth/logout`} method="POST">
        <Button type="submit" variant="ghost" size="sm" className="w-full">
          Sign out
        </Button>
      </form>
    </div>
  )
}

export function AppShell({ children }: { children: ReactNode }) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      {/* Mobile top bar — the fixed sidebar only fits from md up. */}
      <header className="flex items-center justify-between border-b border-slate-200 bg-white p-4 md:hidden">
        <span className="text-lg font-semibold text-slate-900">RegRadar</span>
        <button
          type="button"
          aria-label={mobileNavOpen ? 'Close navigation' : 'Open navigation'}
          aria-expanded={mobileNavOpen}
          onClick={() => setMobileNavOpen((open) => !open)}
          className="flex h-9 w-9 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100"
        >
          {mobileNavOpen ? (
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" d="M4 7h16M4 12h16M4 17h16" />
            </svg>
          )}
        </button>
      </header>

      {mobileNavOpen && (
        <nav className="flex flex-col gap-4 border-b border-slate-200 bg-white p-4 md:hidden">
          <NavLinks onNavigate={() => setMobileNavOpen(false)} />
          <SignOutForm />
        </nav>
      )}

      <nav className="hidden w-56 shrink-0 flex-col border-r border-slate-200 bg-white p-4 md:flex">
        <p className="mb-6 text-lg font-semibold text-slate-900">RegRadar</p>
        <NavLinks />
        <SignOutForm />
      </nav>

      <main className="flex-1 p-4 sm:p-6 lg:p-8">{children}</main>
    </div>
  )
}
