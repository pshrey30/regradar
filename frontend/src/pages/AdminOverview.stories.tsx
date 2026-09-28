import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse, delay } from 'msw'
import { MemoryRouter } from 'react-router-dom'

import { AdminOverview } from './AdminOverview'

const meta = {
  title: 'Pages/AdminOverview',
  component: AdminOverview,
  // AdminOverview renders <Link> elements (Team/Sources panels), which need
  // a Router context that Storybook's isolated rendering doesn't provide by
  // default — same pattern as AdminShell.stories.tsx / UserShell.stories.tsx.
  decorators: [
    (Story) => (
      <MemoryRouter initialEntries={['/overview']}>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof AdminOverview>

export default meta
type Story = StoryObj<typeof meta>

const populatedHandlers = [
  http.get('*/v1/metrics/funnel', () =>
    HttpResponse.json({
      data: [
        { status: 'ingested', count: 4 },
        { status: 'complete', count: 128 },
        { status: 'needs_review', count: 3 },
      ],
      total: 135,
    }),
  ),
  http.get('*/v1/api-keys', () =>
    HttpResponse.json([
      { id: '1', owner_label: 'Jane Admin', role: 'admin', is_active: true, rate_limit_per_minute: 100, key_suffix: 'ab12', created_at: '2026-01-01T00:00:00Z', last_used_at: null },
      { id: '2', owner_label: 'Alex Analyst', role: 'analyst', is_active: true, rate_limit_per_minute: 60, key_suffix: 'cd34', created_at: '2026-01-02T00:00:00Z', last_used_at: null },
    ]),
  ),
  http.get('*/v1/config/sources', () =>
    HttpResponse.json([
      { source: 'SEC', domains: ['financial'], is_active: true, poll_interval_seconds: 300, last_polled_at: '2026-09-27T12:00:00Z' },
      { source: 'FDA', domains: ['clinical'], is_active: false, poll_interval_seconds: 300, last_polled_at: null },
    ]),
  ),
]

// CORRECTED for the installed msw-storybook-addon@3.0.3's real CSF3 API
// (confirmed against its README mid-implementation — v3 dropped
// `parameters.msw.handlers` in favor of a `beforeEach({ msw }) { msw.use(...) }`
// hook per story; `msw.use()` overrides only for the duration of that
// story, same effect the old `parameters.msw.handlers` had).

export const Populated: Story = {
  beforeEach({ msw }) {
    msw.use(...populatedHandlers)
  },
}

export const Empty: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/metrics/funnel', () => HttpResponse.json({ data: [], total: 0 })),
      http.get('*/v1/api-keys', () => HttpResponse.json([])),
      http.get('*/v1/config/sources', () => HttpResponse.json([])),
    )
  },
}

export const Loading: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/metrics/funnel', async () => { await delay('infinite') }),
      http.get('*/v1/api-keys', async () => { await delay('infinite') }),
      http.get('*/v1/config/sources', async () => { await delay('infinite') }),
    )
  },
}

export const ErrorState: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/metrics/funnel', () => new HttpResponse(null, { status: 500 })),
      http.get('*/v1/api-keys', () => new HttpResponse(null, { status: 500 })),
      http.get('*/v1/config/sources', () => new HttpResponse(null, { status: 500 })),
    )
  },
}
