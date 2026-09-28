import { Link, useLocation } from 'react-router-dom'

import { DashboardChrome } from './DashboardChrome'

const USER_NAV_LINKS = [
  { path: '/filings', label: 'Filings' },
  { path: '/activity', label: 'Activity' },
  { path: '/search', label: 'Ask RegRadar' },
  { path: '/webhooks', label: 'Webhooks' },
]

function UserNavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const location = useLocation()
  return (
    <ul className="flex flex-1 flex-col gap-1">
      {USER_NAV_LINKS.map((item) => {
        const isActive = item.path === location.pathname
        return (
          <li key={item.path} className="relative">
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

export function UserShell({ children }: { children: React.ReactNode }) {
  return (
    <DashboardChrome homePath="/filings" navSlot={(onNavigate) => <UserNavLinks onNavigate={onNavigate} />}>
      {children}
    </DashboardChrome>
  )
}
