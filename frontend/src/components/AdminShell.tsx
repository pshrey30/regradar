import { DashboardChrome, NavLinks } from './DashboardChrome'

const ADMIN_NAV_LINKS = [
  { path: '/overview', label: 'Overview' },
  { path: '/filings', label: 'Filings' },
  { path: '/activity', label: 'Activity' },
  { path: '/search', label: 'Ask RegRadar' },
  { path: '/users', label: 'Manage Users' },
  { path: '/source-config', label: 'Source Configuration' },
  { path: '/api-keys', label: 'API Keys' },
  { path: '/metrics', label: 'Metrics & Cost' },
  { path: '/webhooks', label: 'Webhooks' },
]

export function AdminShell({ children }: { children: React.ReactNode }) {
  return (
    <DashboardChrome
      homePath="/overview"
      modeLabel="Admin"
      navSlot={(onNavigate) => <NavLinks items={ADMIN_NAV_LINKS} onNavigate={onNavigate} />}
    >
      {children}
    </DashboardChrome>
  )
}
