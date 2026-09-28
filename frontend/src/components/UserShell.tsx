import { useAuth } from '../auth/useAuth'
import { DashboardChrome, NavLinks, type NavLinkItem } from './DashboardChrome'

const BASE_USER_NAV_LINKS: NavLinkItem[] = [
  { path: '/filings', label: 'Filings' },
  { path: '/activity', label: 'Activity' },
  { path: '/search', label: 'Ask RegRadar' },
]

const METRICS_LINK: NavLinkItem = { path: '/metrics', label: 'Metrics & Cost' }

function UserNavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const { role } = useAuth()

  // Search (backend: POST /v1/filings/search) 403s for executive, so the
  // link is hidden for that role rather than left as a dead end. Metrics
  // (backend: admin-or-eng_lead) is added only for eng_lead — admin
  // already gets it via AdminShell's own link array.
  const links = BASE_USER_NAV_LINKS.filter((item) => !(item.path === '/search' && role === 'executive'))
  if (role === 'eng_lead') {
    links.push(METRICS_LINK)
  }

  return <NavLinks items={links} onNavigate={onNavigate} />
}

export function UserShell({ children }: { children: React.ReactNode }) {
  return (
    <DashboardChrome homePath="/filings" navSlot={(onNavigate) => <UserNavLinks onNavigate={onNavigate} />}>
      {children}
    </DashboardChrome>
  )
}
