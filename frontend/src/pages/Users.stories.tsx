import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse, delay } from 'msw'
import { expect, waitFor, within } from 'storybook/test'

import { Users } from './Users'

// Real field names confirmed from Users.tsx itself:
//   ApiKeyItem: id, owner_label, role, is_active, last_used_at, email, sso_provider
//     (this page's ApiKeyItem does NOT include rate_limit_per_minute/key_suffix —
//     those belong to ApiKeys.tsx's own, separately-declared ApiKeyItem type)
//   InviteItem: id, code_suffix, used_at, used_by_email, created_at
//   InviteCreateResponse: InviteItem & { code: string }
//   queryKey: ['api-keys'], queryFn: GET /v1/api-keys -> ApiKeyItem[]
//   queryKey: ['invites'], queryFn: GET /v1/invites -> InviteItem[]
//   create-invite mutation: POST /v1/invites -> InviteCreateResponse
//   role-change mutation: PATCH /v1/api-keys/{id} { role } -> ApiKeyItem
//
// 403-forbidden trigger (read directly from Users.tsx):
//   const forbidden = query.isError && query.error instanceof ApiError && query.error.status === 403
// `query` here is ONLY the GET /v1/api-keys query — the invites query's error
// is swallowed entirely (InvitesList returns null on query.isError, with a
// comment that the users list above already shows any real error state).
// So the Forbidden story only needs GET /v1/api-keys to return 403; the
// invites endpoint can respond normally. When forbidden is true, the page
// renders a dedicated Card ("You don't have permission to manage users.")
// instead of the generic error Card ("Something went wrong." / the
// ApiError message), and also hides the "Invite a teammate" button and the
// invite-codes Card entirely.
//
// Users.tsx doesn't call useAuth() or render any <Link> — no AuthContext or
// MemoryRouter decorator needed here.

const activeUser = {
  id: 'user-1',
  owner_label: 'Priya Shah',
  role: 'analyst' as const,
  is_active: true,
  last_used_at: '2026-09-26T10:00:00Z',
  email: 'priya@regradar.io',
  sso_provider: null,
}

const ssoUser = {
  id: 'user-2',
  owner_label: 'Marcus Webb',
  role: 'admin' as const,
  is_active: true,
  last_used_at: null,
  email: null,
  sso_provider: 'google',
}

const usedInvite = {
  id: 'invite-1',
  code_suffix: 'ab12',
  used_at: '2026-09-20T00:00:00Z',
  used_by_email: 'priya@regradar.io',
  created_at: '2026-09-01T00:00:00Z',
}

const unusedInvite = {
  id: 'invite-2',
  code_suffix: 'cd34',
  used_at: null,
  used_by_email: null,
  created_at: '2026-09-25T00:00:00Z',
}

const meta = {
  title: 'Pages/Users',
  component: Users,
} satisfies Meta<typeof Users>

export default meta
type Story = StoryObj<typeof meta>

function registerMutationHandlers() {
  return [
    http.post('*/v1/invites', () =>
      HttpResponse.json({
        id: 'invite-new',
        code_suffix: 'zz99',
        used_at: null,
        used_by_email: null,
        created_at: '2026-09-27T00:00:00Z',
        code: 'rrinv_abcdef1234567890',
      }),
    ),
    http.patch('*/v1/api-keys/:id', () => HttpResponse.json(activeUser)),
  ]
}

export const Populated: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/api-keys', () => HttpResponse.json([activeUser, ssoUser])),
      http.get('*/v1/invites', () => HttpResponse.json([usedInvite, unusedInvite])),
      ...registerMutationHandlers(),
    )
  },
}

export const Empty: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/api-keys', () => HttpResponse.json([])),
      http.get('*/v1/invites', () => HttpResponse.json([])),
      ...registerMutationHandlers(),
    )
  },
}

export const Loading: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/api-keys', async () => {
        await delay('infinite')
      }),
      http.get('*/v1/invites', async () => {
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
      http.get('*/v1/invites', () => new HttpResponse(null, { status: 500 })),
      ...registerMutationHandlers(),
    )
  },
}

// The page's forbidden state is driven solely by the GET /v1/api-keys
// query's error being an ApiError with status 403 — the invites query's
// error is swallowed unconditionally. We mock both endpoints as 403 anyway
// (per the task brief) to make sure nothing about the invites response
// leaks into or affects the forbidden panel.
export const Forbidden: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/api-keys', () => new HttpResponse(null, { status: 403 })),
      http.get('*/v1/invites', () => new HttpResponse(null, { status: 403 })),
      ...registerMutationHandlers(),
    )
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)

    await waitFor(() =>
      expect(canvas.getByText('You don’t have permission to manage users.')).toBeInTheDocument(),
    )

    expect(canvas.queryByText('Something went wrong.')).not.toBeInTheDocument()
    expect(canvas.queryByRole('button', { name: 'Invite a teammate' })).not.toBeInTheDocument()
  },
}
