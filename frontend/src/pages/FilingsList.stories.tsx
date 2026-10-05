import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse, delay } from 'msw'
import { MemoryRouter } from 'react-router-dom'

import { AuthContext } from '../auth/AuthContext'
import { FilingsList } from './FilingsList'

// Real field names confirmed from FilingsList.tsx itself:
//   FilingListItem: id, entity_name, filing_type, domain, risk_level,
//     published_at, executive_brief
//   FilingListResponse: data, page, page_size, total
//   PendingFilingItem: id, entity_name, filing_type, source, status,
//     ingested_at, processing_error

const sampleFiling = {
  id: '1',
  entity_name: 'Acme Corp',
  filing_type: '10-K',
  domain: 'financial' as const,
  risk_level: 'high' as const,
  published_at: '2026-09-20T00:00:00Z',
  executive_brief: 'Acme Corp filed its annual report disclosing a material weakness.',
}

const samplePendingFiling = {
  id: '2',
  entity_name: 'Globex Inc',
  filing_type: '8-K',
  source: 'SEC',
  status: 'needs_review',
  ingested_at: '2026-09-26T00:00:00Z',
  processing_error: null,
}

const meta = {
  title: 'Pages/FilingsList',
  component: FilingsList,
  // FilingsList navigates on row click and checks role === 'admin' to show
  // the PendingFilingsPanel — needs both a Router context (same pattern as
  // AdminOverview.stories.tsx) and an AuthContext.Provider (same pattern as
  // Onboarding.stories.tsx). Default here is admin; AsAnalyst below
  // overrides the decorator.
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
        <MemoryRouter initialEntries={['/filings']}>
          <Story />
        </MemoryRouter>
      </AuthContext.Provider>
    ),
  ],
} satisfies Meta<typeof FilingsList>

export default meta
type Story = StoryObj<typeof meta>

export const Populated: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/filings/pending', () => HttpResponse.json({ data: [] })),
      http.get('*/v1/filings', () =>
        HttpResponse.json({ data: [sampleFiling], page: 1, page_size: 10, total: 1 }),
      ),
    )
  },
}

export const Empty: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/filings/pending', () => HttpResponse.json({ data: [] })),
      http.get('*/v1/filings', () =>
        HttpResponse.json({ data: [], page: 1, page_size: 10, total: 0 }),
      ),
    )
  },
}

export const Loading: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/filings/pending', async () => {
        await delay('infinite')
      }),
      http.get('*/v1/filings', async () => {
        await delay('infinite')
      }),
    )
  },
}

export const ErrorState: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/filings/pending', () => HttpResponse.json({ data: [] })),
      http.get('*/v1/filings', () => new HttpResponse(null, { status: 500 })),
    )
  },
}

// Role variants: admin sees the PendingFilingsPanel (backed by
// /v1/filings/pending), analyst does not — the panel component isn't even
// mounted for a non-admin role, so no /pending mock is needed there.

export const AsAdmin: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/filings/pending', () =>
        HttpResponse.json({ data: [samplePendingFiling], page: 1, page_size: 10, total: 1 }),
      ),
      http.get('*/v1/filings', () =>
        HttpResponse.json({ data: [sampleFiling], page: 1, page_size: 10, total: 1 }),
      ),
    )
  },
}

export const AsAnalyst: Story = {
  // Story-level decorators compose with (wrap inside) the meta-level ones,
  // they don't replace them — the meta decorator already supplies
  // MemoryRouter, so only AuthContext needs overriding here. Nesting a
  // second MemoryRouter throws ("You cannot render a <Router> inside
  // another <Router>").
  decorators: [
    (Story) => (
      <AuthContext.Provider
        value={{
          status: 'authenticated',
          role: 'analyst',
          organizationId: 'org-1',
          displayName: 'Alex Analyst',
          organizationSetupComplete: true,
        }}
      >
        <Story />
      </AuthContext.Provider>
    ),
  ],
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/filings', () =>
        HttpResponse.json({ data: [sampleFiling], page: 1, page_size: 10, total: 1 }),
      ),
    )
  },
}
