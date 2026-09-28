import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse, delay } from 'msw'
import { expect, userEvent, waitFor, within } from 'storybook/test'

import { ApiKeys, type ApiKeyItem } from './ApiKeys'

// Real field names confirmed from ApiKeys.tsx itself:
//   ApiKeyItem: id, owner_label, role, is_active, rate_limit_per_minute,
//     key_suffix, created_at, last_used_at
//   ApiKeyCreateResponse: ApiKeyItem & { key: string } (not exported from
//     ApiKeys.tsx, so it's inlined here as a plain object matching that shape)
//   queryKey: ['api-keys'], queryFn: GET /v1/api-keys -> ApiKeyItem[]
//   create mutation: POST /v1/api-keys { owner_label, role } -> ApiKeyCreateResponse
//   revoke mutation: DELETE /v1/api-keys/{id}
//
// ApiKeys doesn't call useAuth() or render any <Link> — the 403/"forbidden"
// path is driven entirely by the GET /v1/api-keys response status, not by
// role read from context. So unlike FilingsList/Activity, no AuthContext or
// MemoryRouter decorator is needed here.

const activeApiKey: ApiKeyItem = {
  id: 'key-1',
  owner_label: 'CI pipeline',
  role: 'eng_lead',
  is_active: true,
  rate_limit_per_minute: 120,
  key_suffix: 'ab12',
  created_at: '2026-09-01T00:00:00Z',
  last_used_at: '2026-09-26T10:00:00Z',
}

const revokedApiKey: ApiKeyItem = {
  id: 'key-2',
  owner_label: 'Legacy analyst laptop',
  role: 'analyst',
  is_active: false,
  rate_limit_per_minute: 60,
  key_suffix: 'cd34',
  created_at: '2026-01-15T00:00:00Z',
  last_used_at: null,
}

const meta = {
  title: 'Pages/ApiKeys',
  component: ApiKeys,
} satisfies Meta<typeof ApiKeys>

export default meta
type Story = StoryObj<typeof meta>

// Every story below also registers the create/revoke mutation handlers
// (even the read-only ones) so opening the "Create key" modal or clicking
// "Revoke" never 404s if a reviewer interacts with the page, per the task
// brief.

function registerMutationHandlers() {
  return [
    http.post('*/v1/api-keys', () =>
      HttpResponse.json({
        id: 'key-new',
        owner_label: 'New integration',
        role: 'analyst',
        is_active: true,
        rate_limit_per_minute: 60,
        key_suffix: 'zz99',
        created_at: '2026-09-27T00:00:00Z',
        last_used_at: null,
        key: 'rr_live_abcdef1234567890',
      }),
    ),
    http.delete('*/v1/api-keys/:id', () => new HttpResponse(null, { status: 204 })),
  ]
}

export const Populated: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/api-keys', () => HttpResponse.json([activeApiKey, revokedApiKey])),
      ...registerMutationHandlers(),
    )
  },
}

export const Empty: Story = {
  beforeEach({ msw }) {
    msw.use(http.get('*/v1/api-keys', () => HttpResponse.json([])), ...registerMutationHandlers())
  },
}

export const Loading: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/api-keys', async () => {
        await delay('infinite')
      }),
      ...registerMutationHandlers(),
    )
  },
}

export const ErrorState: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/api-keys', () => new HttpResponse(null, { status: 500 })),
      ...registerMutationHandlers(),
    )
  },
}

// Interaction test: exercises the full revoke flow (mirrors
// ChipInput.stories.tsx's AddAndRemove pattern) — click "Revoke" on the
// active key, confirm in the modal, and verify the row disappears once the
// list query refetches. The DELETE handler mutates local state so the
// subsequent GET refetch (triggered by the mutation's queryClient
// invalidation) reflects the revocation.
export const RevokeFlow: Story = {
  beforeEach({ msw }) {
    let keys: ApiKeyItem[] = [activeApiKey]
    msw.use(
      http.get('*/v1/api-keys', () => HttpResponse.json(keys)),
      http.delete('*/v1/api-keys/:id', () => {
        keys = []
        return new HttpResponse(null, { status: 204 })
      }),
      http.post('*/v1/api-keys', () =>
        HttpResponse.json({
          id: 'key-new',
          owner_label: 'New integration',
          role: 'analyst',
          is_active: true,
          rate_limit_per_minute: 60,
          key_suffix: 'zz99',
          created_at: '2026-09-27T00:00:00Z',
          last_used_at: null,
          key: 'rr_live_abcdef1234567890',
        }),
      ),
    )
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)

    await waitFor(() => expect(canvas.getByText('CI pipeline')).toBeInTheDocument())

    await userEvent.click(canvas.getByRole('button', { name: 'Revoke' }))
    await userEvent.click(canvas.getByRole('button', { name: 'Revoke key' }))

    await waitFor(() => expect(canvas.queryByText('CI pipeline')).not.toBeInTheDocument())
  },
}
