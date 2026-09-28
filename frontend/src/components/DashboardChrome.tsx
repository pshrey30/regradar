import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { API_BASE_URL } from '../lib/api'
import { Button } from './Button'

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

export interface DashboardChromeProps {
  children: ReactNode
  homePath: string
  navSlot: (onNavigate?: () => void) => ReactNode
  modeLabel?: string
}

export function DashboardChrome({ children, homePath, navSlot, modeLabel }: DashboardChromeProps) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
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
          {navSlot(() => setMobileNavOpen(false))}
          <SignOutForm />
        </nav>
      )}

      <nav className="hidden w-56 shrink-0 flex-col border-r border-slate-200 bg-white p-4 md:flex">
        <Link to={homePath} className="mb-1 block font-mono text-sm font-semibold tracking-[0.2em] text-slate-900">
          REGRADAR
        </Link>
        {modeLabel && (
          <span className="mb-5 inline-flex w-fit items-center rounded-full bg-primary-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary-700">
            {modeLabel}
          </span>
        )}
        {navSlot()}
        <SignOutForm />
      </nav>

      <main className="flex-1 p-4 sm:p-6 lg:p-8">{children}</main>
    </div>
  )
}
