import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse, delay } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { AuthContext } from '../auth/AuthContext'
import { FilingDetail } from './FilingDetail'

// Real field names confirmed from FilingDetail.tsx and
// api/routers/filings.py's get_filing/get_filing_brief:
//   FilingDetailResponse: id, entity_name, filing_type, domain, risk_level,
//     priority_score, published_at, status, brief ({ executive_brief } | null),
//     similar_filings, extraction?
//   PersonaBriefResponse: persona, summary
//   queryKey ['filing', filingId] -> GET /v1/filings/{id}
//   queryKey ['filing-brief', filingId, persona] -> GET /v1/filings/{id}/brief[?persona=]
//
// IMPORTANT: `extraction` — not `brief` — is the field that's entirely
// absent (not null) from the /v1/filings/{id} response for the Executive
// role; see get_filing's docstring ("extraction is entirely absent from
// the response for the Executive role ... that's why this route builds a
// plain dict instead of using a fixed response_model"). The /brief
// endpoint has a different, always-present mechanism for Executive: it
// silently narrows any requested persona to "cco" (get_filing_brief's
// docstring), it never omits or 404s the brief specifically because of
// role. So the dedicated "Executive" story below demonstrates the real
// absent-key behavior on the filing-detail response (extraction), with
// the brief endpoint mocked to return the cco-narrowed summary that a
// real Executive-role caller would actually receive.

const sampleFiling = {
  id: '1',
  entity_name: 'Acme Corp',
  filing_type: '10-K',
  domain: 'financial' as const,
  risk_level: 'high' as const,
  priority_score: 0.82,
  published_at: '2026-09-20T00:00:00Z',
  status: 'complete',
  brief: { executive_brief: 'Acme Corp filed its annual report disclosing a material weakness.' },
  similar_filings: [
    {
      id: '2',
      entity_name: 'Globex Inc',
      filing_type: '8-K',
      published_at: '2026-09-18T00:00:00Z',
    },
  ],
  extraction: {
    obligations: [
      { description: 'Remediate the disclosed material weakness within 90 days.', source_citation: 'Item 9A' },
    ],
    deadlines: [{ description: 'Remediation plan due', date: '2026-12-20' }],
    risk_flags: ['material_weakness'],
    affected_products: ['Consumer Lending'],
    key_entities: ['Acme Corp'],
    competitor_mentions: [],
  },
}

const sampleBrief = { persona: 'executive', summary: 'Acme Corp disclosed a material weakness in internal controls.' }

const meta = {
  title: 'Pages/FilingDetail',
  component: FilingDetail,
  decorators: [
    (Story) => (
      <MemoryRouter initialEntries={['/filings/1']}>
        <Routes>
          <Route path="/filings/:filingId" element={<Story />} />
        </Routes>
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof FilingDetail>

export default meta
type Story = StoryObj<typeof meta>

export const Populated: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/filings/1', () => HttpResponse.json(sampleFiling)),
      http.get('*/v1/filings/1/brief', () => HttpResponse.json(sampleBrief)),
    )
  },
}

export const Empty: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/filings/1', () =>
        HttpResponse.json({
          ...sampleFiling,
          brief: null,
          similar_filings: [],
          extraction: { ...sampleFiling.extraction, obligations: [], deadlines: [] },
        }),
      ),
      http.get('*/v1/filings/1/brief', () => new HttpResponse(null, { status: 404 })),
    )
  },
}

export const Loading: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/filings/1', async () => {
        await delay('infinite')
      }),
      http.get('*/v1/filings/1/brief', async () => {
        await delay('infinite')
      }),
    )
  },
}

export const ErrorState: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/filings/1', () => new HttpResponse(null, { status: 500 })),
      http.get('*/v1/filings/1/brief', () => new HttpResponse(null, { status: 500 })),
    )
  },
}

// Executive-role: `extraction` is entirely absent (the key itself, not
// merely `null`) from the /v1/filings/{id} response — the real backend
// behavior confirmed in get_filing's docstring. FilingDetail.tsx checks
// `filing.extraction !== undefined` (not just truthiness) to decide
// whether to render the extraction Card at all, so this story only
// renders correctly if the mock response genuinely omits the key rather
// than setting it to null. The brief endpoint is mocked to return the
// cco-narrowed summary a real Executive-role caller receives (see
// get_filing_brief's docstring); FilingDetail doesn't read AuthContext
// itself, so this override exists to document which persona/role this
// story represents, not because the component branches on it.
const { extraction: _omittedExtraction, ...executiveFiling } = sampleFiling

export const AsExecutiveNoExtraction: Story = {
  decorators: [
    (Story) => (
      <AuthContext.Provider
        value={{
          status: 'authenticated',
          role: 'executive',
          organizationId: 'org-1',
          displayName: 'Erin Executive',
          organizationSetupComplete: true,
        }}
      >
        <Story />
      </AuthContext.Provider>
    ),
  ],
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/filings/1', () => HttpResponse.json(executiveFiling)),
      http.get('*/v1/filings/1/brief', () => HttpResponse.json({ persona: 'cco', summary: sampleBrief.summary })),
    )
  },
}
