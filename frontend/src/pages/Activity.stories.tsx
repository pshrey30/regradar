import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse, delay } from 'msw'
import { MemoryRouter } from 'react-router-dom'

import { AuthContext } from '../auth/AuthContext'
import { Activity } from './Activity'

// Real field names confirmed from Activity.tsx itself:
//   ActivityItem: id, filing_id, entity_name, filing_type, domain,
//     risk_level, channel ('slack' | 'email' | 'webhook'),
//     status ('pending' | 'sent' | 'failed' | 'retrying'), is_fallback,
//     at, error_message
//   queryKey: ['activity'], queryFn: GET /v1/activity -> ActivityItem[]
//
// allowedDomainsForRole(role) (src/auth/domainScope.ts) only drives the
// descriptive text above the list ("Alerts for X filings..." vs "Every
// alert..."), it doesn't filter query results client-side — admin/executive
// get null (unrestricted, generic copy), eng_lead is restricted to
// ['engineering'] (scoped copy naming that domain).

const sentEngineeringAlert = {
  id: '1',
  filing_id: 'f-1',
  entity_name: 'Acme Corp',
  filing_type: '10-K',
  domain: 'engineering' as const,
  risk_level: 'high' as const,
  channel: 'slack' as const,
  status: 'sent' as const,
  is_fallback: false,
  at: '2026-09-26T14:00:00Z',
  error_message: null,
}

const failedFinancialAlert = {
  id: '2',
  filing_id: 'f-2',
  entity_name: 'Globex Inc',
  filing_type: '8-K',
  domain: 'financial' as const,
  risk_level: 'critical' as const,
  channel: 'email' as const,
  status: 'failed' as const,
  is_fallback: true,
  at: '2026-09-25T09:30:00Z',
  error_message: 'HTTP 500',
}

const meta = {
  title: 'Pages/Activity',
  component: Activity,
  // Activity calls useAuth() for role and links to /filings/:id — needs
  // both AuthContext and a Router context (same pattern as
  // FilingsList.stories.tsx). Default here is admin; AsEngLead below
  // overrides only the AuthContext.Provider, per the established pattern —
  // nesting a second MemoryRouter throws ("You cannot render a <Router>
  // inside another <Router>").
  decorators: [
    (Story) => (
      <AuthContext.Provider
        value={{
          status: 'authenticated',
          role: 'admin',
          organizationId: 'org-1',
          displayName: 'Jane Admin',
          organizationSetupComplete: true,
        }}
      >
        <MemoryRouter initialEntries={['/activity']}>
          <Story />
        </MemoryRouter>
      </AuthContext.Provider>
    ),
  ],
} satisfies Meta<typeof Activity>

export default meta
type Story = StoryObj<typeof meta>

export const Populated: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/activity', () =>
        HttpResponse.json([sentEngineeringAlert, failedFinancialAlert]),
      ),
    )
  },
}

export const Empty: Story = {
  beforeEach({ msw }) {
    msw.use(http.get('*/v1/activity', () => HttpResponse.json([])))
  },
}

export const Loading: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/activity', async () => {
        await delay('infinite')
      }),
    )
  },
}

export const ErrorState: Story = {
  beforeEach({ msw }) {
    msw.use(http.get('*/v1/activity', () => new HttpResponse(null, { status: 500 })))
  },
}

// Role variants: allowedDomainsForRole(role) drives the descriptive copy
// above the list. Admin is unrestricted ("Every alert..."); eng_lead is
// scoped to the engineering domain ("Alerts for Engineering filings...").
// Both render the same mock data so the visible difference is the copy
// itself, not the list contents (the API isn't filtered client-side).

export const AsAdmin: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/activity', () =>
        HttpResponse.json([sentEngineeringAlert, failedFinancialAlert]),
      ),
    )
  },
}

export const AsEngLead: Story = {
  decorators: [
    (Story) => (
      <AuthContext.Provider
        value={{
          status: 'authenticated',
          role: 'eng_lead',
          organizationId: 'org-1',
          displayName: 'Sam Engineer',
          organizationSetupComplete: true,
        }}
      >
        <Story />
      </AuthContext.Provider>
    ),
  ],
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/activity', () =>
        HttpResponse.json([sentEngineeringAlert, failedFinancialAlert]),
      ),
    )
  },
}
