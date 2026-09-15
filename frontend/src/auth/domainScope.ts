import type { DomainValue } from '../components/Badge'
import type { Role } from './AuthContext'

// Mirrors the backend's real enforcement (core/domain_scope.py) — this is
// display-only: it decides what the Filings/Activity filter UI offers and
// whether to show a "scoped to X" note, never a security boundary on its
// own. `null` means unrestricted.
const ROLE_DOMAIN_RESTRICTIONS: Record<Role, DomainValue[] | null> = {
  admin: null,
  analyst: ['financial'],
  executive: null,
  legal_counsel: ['clinical', 'environmental'],
  eng_lead: ['engineering'],
}

export function allowedDomainsForRole(role: Role | null): DomainValue[] | null {
  if (!role) return null
  return ROLE_DOMAIN_RESTRICTIONS[role]
}
