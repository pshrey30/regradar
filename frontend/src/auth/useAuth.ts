import { useContext } from 'react'

import { AuthContext, type Role } from './AuthContext'

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}

// Security & Access Document's permission matrix, condensed to what the
// nav shell needs — FE-03 through FE-09's own screens aren't built yet
// (out of this ticket's scope), so this only gates which nav *links* show,
// not any route's actual data.
const _NAV_ITEM_ROLES: Record<string, Role[] | 'all'> = {
  filings: 'all',
  search: ['admin', 'analyst', 'legal_counsel', 'eng_lead'], // not executive (API-06's own 403)
  webhooks: 'all',
  api_keys: ['admin'],
  metrics: ['admin', 'eng_lead'],
  source_config: ['admin'],
}

export function canSeeNavItem(role: Role | null, item: keyof typeof _NAV_ITEM_ROLES): boolean {
  if (!role) return false
  const allowed = _NAV_ITEM_ROLES[item]
  return allowed === 'all' || allowed.includes(role)
}
