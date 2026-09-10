import { useContext } from 'react'

import { AuthContext, type Role } from './AuthContext'

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}

// Security & Access Document's permission matrix, condensed to what the
// nav shell needs. Every protected screen's real data access is already
// enforced server-side (RLS + explicit role checks) regardless of this
// table — App.tsx's ProtectedRoute additionally reuses it as a route-level
// guard (see NAV_ITEM_TO_PATH below) so a blocked role is redirected
// before the page renders, rather than relying solely on the nav link
// being hidden plus the backend's eventual 403.
const _NAV_ITEM_ROLES: Record<string, Role[] | 'all'> = {
  filings: 'all',
  search: ['admin', 'analyst', 'legal_counsel', 'eng_lead'], // not executive (API-06's own 403)
  webhooks: 'all',
  api_keys: ['admin'],
  metrics: ['admin', 'eng_lead'],
  source_config: ['admin'],
  users: ['admin'],
}

export type NavItem = keyof typeof _NAV_ITEM_ROLES

export function canSeeNavItem(role: Role | null, item: NavItem): boolean {
  if (!role) return false
  const allowed = _NAV_ITEM_ROLES[item]
  return allowed === 'all' || allowed.includes(role)
}
