import { useState, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { canSeeNavItem, useAuth } from '../auth/useAuth'
import { API_BASE_URL } from '../lib/api'
import { Button } from './Button'

const _NAV_LINKS: { key: Parameters<typeof canSeeNavItem>[1]; label: string; path?: string }[] = [
  { key: 'filings', label: 'Filings', path: '/filings' },
  { key: 'search', label: 'Ask RegRadar', path: '/search' },
  { key: 'webhooks', label: 'Webhooks', path: '/webhooks' },
  { key: 'api_keys', label: 'API Keys', path: '/api-keys' },
  { key: 'metrics', label: 'Metrics & Cost', path: '/metrics' },
  { key: 'source_config', label: 'Source Configuration', path: '/source-config' },
  { key: 'users', label: 'Manage Users', path: '/users' },
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
          <li key={item.key} className="relative">
            <Link
              to={item.path}
              onClick={onNavigate}
              className={[
                'block rounded-md px-3 py-2 text-sm transition-all duration-150',
                isActive
                  ? 'bg-primary-50 font-medium text-primary-700 translate-x-0.5'
                  : 'text-slate-600 hover:translate-x-0.5 hover:bg-slate-100',
              ].join(' ')}
            >
              {item.label}
            </Link>
            {isActive && (
              <span className="absolute -left-1 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-primary-600" />
            )}
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
      <Link
        to="/profile"
        className="mb-2 block truncate rounded-md px-1 text-xs text-slate-500 transition-colors hover:text-primary-600"
      >
        {displayName}
      </Link>
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
        <span className="font-mono text-sm font-semibold tracking-[0.2em] text-slate-900">
          REGRADAR
        </span>
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
        <Link
          to="/filings"
          className="mb-6 block font-mono text-sm font-semibold tracking-[0.2em] text-slate-900"
        >
          REGRADAR
        </Link>
        <NavLinks />
        <SignOutForm />
      </nav>

      <main className="flex-1 p-4 sm:p-6 lg:p-8">{children}</main>
    </div>
  )
}
