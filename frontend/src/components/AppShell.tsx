import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { canSeeNavItem, useAuth } from '../auth/useAuth'
import { API_BASE_URL } from '../lib/api'
import { Button } from './Button'

// `path` is only set for a nav item whose screen actually exists yet
// (FE-06 through FE-09's screens aren't built — those stay inert text,
// same as every nav item was before FE-05 added the first real
// destination to click *to* from elsewhere in the nav).
const _NAV_LINKS: { key: Parameters<typeof canSeeNavItem>[1]; label: string; path?: string }[] = [
  { key: 'filings', label: 'Filings', path: '/' },
  { key: 'search', label: 'Ask RegRadar', path: '/search' },
  { key: 'webhooks', label: 'Webhooks' },
  { key: 'api_keys', label: 'API Keys' },
  { key: 'metrics', label: 'Metrics & Cost' },
  { key: 'source_config', label: 'Source Configuration' },
]

export function AppShell({ children }: { children: ReactNode }) {
  const { role, displayName } = useAuth()

  return (
    <div className="flex min-h-screen">
      <nav className="flex w-56 flex-col border-r border-slate-200 bg-white p-4">
        <p className="mb-6 text-lg font-semibold text-slate-900">RegRadar</p>
        <ul className="flex flex-1 flex-col gap-1">
          {_NAV_LINKS.filter((item) => canSeeNavItem(role, item.key)).map((item) =>
            item.path ? (
              <li key={item.key}>
                <Link
                  to={item.path}
                  className="block rounded-md px-3 py-2 text-sm text-slate-600 hover:bg-slate-100"
                >
                  {item.label}
                </Link>
              </li>
            ) : (
              <li key={item.key}>
                <span className="block cursor-default rounded-md px-3 py-2 text-sm text-slate-600 hover:bg-slate-100">
                  {item.label}
                </span>
              </li>
            ),
          )}
        </ul>
        <div className="border-t border-slate-200 pt-4">
          <p className="mb-2 truncate text-xs text-slate-500">{displayName}</p>
          <form action={`${API_BASE_URL}/v1/auth/logout`} method="POST">
            <Button type="submit" variant="ghost" size="sm" className="w-full">
              Sign out
            </Button>
          </form>
        </div>
      </nav>
      <main className="flex-1 p-8">{children}</main>
    </div>
  )
}
