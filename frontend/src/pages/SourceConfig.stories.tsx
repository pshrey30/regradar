import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse, delay } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { expect, userEvent, within } from 'storybook/test'

import { SourceConfig, type SourceConfigItem } from './SourceConfig'

// Real field names confirmed from SourceConfig.tsx itself:
//   SourceConfigItem: source, domains, is_active, poll_interval_seconds,
//     last_polled_at
//   queryKey: ['config', 'sources'], queryFn: GET /v1/config/sources ->
//     SourceConfigItem[]
//   save mutation: POST /v1/config/sources
//     { sources: string[], domains: string[] } -> SourceConfigItem[]
//     (on success, the response replaces the ['config', 'sources'] query
//     data directly via queryClient.setQueryData)
//   poll mutation: POST /v1/config/sources/FDA/poll ->
//     { source, new_filing_count, last_polled_at, processing_filing_ids }
//   fetch-now mutation: POST /v1/config/sources/fetch-now
//     { sources: string[] } -> { results: [{source, new_filing_count}],
//     processing_filing_ids }
//
// SourceConfig renders a <Link to="/filings"> in its poll/fetch-now result
// messages when anything is now processing — needs a MemoryRouter, unlike
// ApiKeys.stories.tsx (no <Link> there).

// Same two-source sample data as AdminOverview.stories.tsx's SourcesPanel
// mock (Task 5), reused verbatim for visual/data consistency across the two
// pages that both display source health: SEC active, FDA inactive.
const sampleSources: SourceConfigItem[] = [
  { source: 'SEC', domains: ['financial'], is_active: true, poll_interval_seconds: 300, last_polled_at: '2026-09-27T12:00:00Z', feed_url: null },
  { source: 'FDA', domains: ['clinical'], is_active: false, poll_interval_seconds: 300, last_polled_at: null, feed_url: 'https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml' },
]

const meta = {
  title: 'Pages/SourceConfig',
  component: SourceConfig,
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof SourceConfig>

export default meta
type Story = StoryObj<typeof meta>

// Every story also registers the save mutation handler (even the read-only
// ones) so a reviewer toggling a source/domain and clicking "Save Changes"
// never 404s, per the established ApiKeys.stories.tsx pattern.
function registerMutationHandlers() {
  return [
    http.post('*/v1/config/sources', () => HttpResponse.json(sampleSources)),
  ]
}

export const Populated: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/config/sources', () => HttpResponse.json(sampleSources)),
      ...registerMutationHandlers(),
    )
  },
}

export const Empty: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/config/sources', () => HttpResponse.json([])),
      ...registerMutationHandlers(),
    )
  },
}

export const Loading: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/config/sources', async () => {
        await delay('infinite')
      }),
      ...registerMutationHandlers(),
    )
  },
}

// FDA has no on/off toggle (see SourceConfig.tsx's TOGGLE_SOURCES comment)
// — it's polled on demand via this button instead. Drives the real click
// through POST /v1/config/sources/FDA/poll, which now also auto-processes
// every pending filing in the background — this story's response includes
// two processing_filing_ids, so the result message and the "View live
// progress" link to /filings both need to appear.
export const PollFdaNow: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/config/sources', () => HttpResponse.json(sampleSources)),
      ...registerMutationHandlers(),
      http.post('*/v1/config/sources/FDA/poll', () =>
        HttpResponse.json({
          source: 'FDA',
          new_filing_count: 3,
          last_polled_at: '2026-09-30T00:00:00Z',
          processing_filing_ids: ['f-1', 'f-2'],
        }),
      ),
    )
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    await userEvent.click(await canvas.findByRole('button', { name: 'Poll now' }))
    await expect(
      await canvas.findByText(/Polled — 3 new filings found\. Now processing 2 filings live/),
    ).toBeInTheDocument()
    await expect(canvas.getByRole('link', { name: /View live progress/ })).toHaveAttribute(
      'href',
      '/filings',
    )
  },
}

// The Regulators panel's own "Fetch now" — operates on whichever
// SEC/FINRA checkboxes are currently saved (sampleSources has only SEC
// active), fetching both/either in one call via POST
// /v1/config/sources/fetch-now, then likewise auto-processing everything
// pending in the background.
export const FetchRegulatorsNow: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/config/sources', () => HttpResponse.json(sampleSources)),
      ...registerMutationHandlers(),
      http.post('*/v1/config/sources/fetch-now', () =>
        HttpResponse.json({
          results: [{ source: 'SEC', new_filing_count: 2 }],
          processing_filing_ids: ['f-1', 'f-2'],
        }),
      ),
    )
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    await userEvent.click(await canvas.findByRole('button', { name: 'Fetch now' }))
    await expect(await canvas.findByText(/Fetched — SEC 2 new\. Now processing 2 filings live/)).toBeInTheDocument()
    await expect(canvas.getByRole('link', { name: /View live progress/ })).toHaveAttribute(
      'href',
      '/filings',
    )
  },
}

export const ErrorState: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/config/sources', () => new HttpResponse(null, { status: 500 })),
      ...registerMutationHandlers(),
    )
  },
}
