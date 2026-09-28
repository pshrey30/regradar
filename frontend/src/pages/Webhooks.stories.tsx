import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse, delay } from 'msw'
import { expect, waitFor, within } from 'storybook/test'

import { Webhooks } from './Webhooks'

// Real field names confirmed from Webhooks.tsx itself:
//   WebhookItem: id, url, is_active, filter_domain, filter_min_risk,
//     created_at, last_delivery_status ('pending'|'sent'|'failed'|'retrying'|null),
//     last_delivery_at, recent_failure_count
//   WebhookCreateResponse: WebhookItem & { hmac_secret: string }
//   queryKey: ['webhooks'], queryFn: GET /v1/webhooks -> WebhookItem[]
//   create mutation: POST /v1/webhooks
//     { url, filter_domain: string|null, filter_min_risk: string|null } ->
//     WebhookCreateResponse (shows the hmac_secret once, in the modal)
//   delete mutation: DELETE /v1/webhooks/{id}
//
// 403-forbidden trigger (read directly from Webhooks.tsx):
//   const forbidden = query.isError && query.error instanceof ApiError && query.error.status === 403
// This IS a genuinely distinct UI branch, same shape as Users.tsx's Forbidden
// state: when forbidden is true, the page renders a dedicated Card ("You
// don't have permission to view webhooks.") instead of the generic error
// Card ("Something went wrong loading webhooks." / the ApiError message),
// and also hides the "Register webhook" button entirely. So a dedicated
// Forbidden story is added below, distinct from ErrorState (which covers the
// generic non-403 error case).
//
// Webhooks.tsx doesn't call useAuth() or render any <Link> — no AuthContext
// or MemoryRouter decorator needed here.

const healthyWebhook = {
  id: 'webhook-1',
  url: 'https://example.com/regradar-webhook',
  is_active: true,
  filter_domain: 'financial' as const,
  filter_min_risk: 'high' as const,
  created_at: '2026-09-01T00:00:00Z',
  last_delivery_status: 'sent' as const,
  last_delivery_at: '2026-09-27T10:00:00Z',
  recent_failure_count: 0,
}

const failingWebhook = {
  id: 'webhook-2',
  url: 'https://ops.internal.example.org/hooks/regradar',
  is_active: false,
  filter_domain: null,
  filter_min_risk: null,
  created_at: '2026-08-15T00:00:00Z',
  last_delivery_status: 'failed' as const,
  last_delivery_at: '2026-09-26T04:30:00Z',
  recent_failure_count: 3,
}

const meta = {
  title: 'Pages/Webhooks',
  component: Webhooks,
} satisfies Meta<typeof Webhooks>

export default meta
type Story = StoryObj<typeof meta>

function registerMutationHandlers() {
  return [
    http.post('*/v1/webhooks', () =>
      HttpResponse.json({
        ...healthyWebhook,
        id: 'webhook-new',
        hmac_secret: 'whsec_abcdef1234567890',
      }),
    ),
    http.delete('*/v1/webhooks/:id', () => new HttpResponse(null, { status: 204 })),
  ]
}

export const Populated: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/webhooks', () => HttpResponse.json([healthyWebhook, failingWebhook])),
      ...registerMutationHandlers(),
    )
  },
}

export const Empty: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/webhooks', () => HttpResponse.json([])),
      ...registerMutationHandlers(),
    )
  },
}

export const Loading: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/webhooks', async () => {
        await delay('infinite')
      }),
      ...registerMutationHandlers(),
    )
  },
}

export const ErrorState: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/webhooks', () => new HttpResponse(null, { status: 500 })),
      ...registerMutationHandlers(),
    )
  },
}

export const Forbidden: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/webhooks', () => new HttpResponse(null, { status: 403 })),
      ...registerMutationHandlers(),
    )
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)

    await waitFor(() =>
      expect(canvas.getByText('You don’t have permission to view webhooks.')).toBeInTheDocument(),
    )

    expect(canvas.queryByText('Something went wrong loading webhooks.')).not.toBeInTheDocument()
    expect(canvas.queryByRole('button', { name: 'Register webhook' })).not.toBeInTheDocument()
  },
}
