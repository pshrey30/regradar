import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse, delay } from 'msw'

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
//
// SourceConfig doesn't render any <Link> or read AuthContext, so no
// MemoryRouter decorator is needed here (same reasoning as ApiKeys.stories.tsx).

// Same two-source sample data as AdminOverview.stories.tsx's SourcesPanel
// mock (Task 5), reused verbatim for visual/data consistency across the two
// pages that both display source health: SEC active, FDA inactive.
const sampleSources: SourceConfigItem[] = [
  { source: 'SEC', domains: ['financial'], is_active: true, poll_interval_seconds: 300, last_polled_at: '2026-09-27T12:00:00Z' },
  { source: 'FDA', domains: ['clinical'], is_active: false, poll_interval_seconds: 300, last_polled_at: null },
]

const meta = {
  title: 'Pages/SourceConfig',
  component: SourceConfig,
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

export const ErrorState: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/config/sources', () => new HttpResponse(null, { status: 500 })),
      ...registerMutationHandlers(),
    )
  },
}
