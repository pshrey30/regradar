import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse, delay } from 'msw'

import { Profile } from './Profile'

// Real field names confirmed from Profile.tsx itself:
//   MeResponse (not exported from Profile.tsx, inlined here matching its
//   shape): role, organization_id, display_name, email, has_password
//     - organization_setup_complete is NOT used by Profile.tsx (that field
//       belongs elsewhere in the app) — omitted here.
//   queryKey: ['me', 'profile'], queryFn: GET /v1/me -> MeResponse
//   NameForm mutation: PATCH /v1/me { display_name } -> MeResponse
//   PasswordForm mutation: POST /v1/auth/change-password
//     { current_password, new_password } -> (response body unused by the
//     component; a bare 200 JSON object is fine)
//
// Profile.tsx doesn't call useAuth() or render any <Link> — no AuthContext
// or MemoryRouter decorator needed here (unlike FilingsList/Activity).
//
// No meaningful "empty" state for a single-record profile page (per the
// task brief) — Populated/Loading/ErrorState only, 3 states.

const me = {
  role: 'analyst',
  organization_id: 'org-1',
  display_name: 'Jordan Lee',
  email: 'jordan.lee@example.com',
  has_password: true,
}

// Registered on every story (even read-only ones) so the form submit paths
// don't 404 if a reviewer interacts with the page or a play function is
// added later.
function registerMutationHandlers() {
  return [
    http.patch('*/v1/me', () =>
      HttpResponse.json({ ...me, display_name: 'Jordan Lee (updated)' }),
    ),
    http.post('*/v1/auth/change-password', () => HttpResponse.json({ ok: true })),
  ]
}

const meta = {
  title: 'Pages/Profile',
  component: Profile,
} satisfies Meta<typeof Profile>

export default meta
type Story = StoryObj<typeof meta>

export const Populated: Story = {
  beforeEach({ msw }) {
    msw.use(http.get('*/v1/me', () => HttpResponse.json(me)), ...registerMutationHandlers())
  },
}

export const Loading: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/me', async () => {
        await delay('infinite')
      }),
      ...registerMutationHandlers(),
    )
  },
}

export const ErrorState: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/me', () => new HttpResponse(null, { status: 500 })),
      ...registerMutationHandlers(),
    )
  },
}
