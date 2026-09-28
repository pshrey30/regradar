import { http, HttpResponse } from 'msw'

// Every handler here is a REASONABLE DEFAULT for a story that doesn't
// care about this endpoint's exact response. These are installed
// globally in `.storybook/preview.ts` via `beforeEach({ msw }) { msw.use(...handlers) }`.
// Individual stories override a specific endpoint the same way, in their
// own `beforeEach({ msw }) { msw.use(...) }` — MSW's `msw.use()` handlers
// take precedence over the ones installed here, for the duration of that
// story only. Wildcard base (`*/v1/...`) so this works regardless of how
// API_BASE_URL resolves inside Storybook's browser context.

export const handlers = [
  http.get('*/v1/me', () =>
    HttpResponse.json({
      role: 'admin',
      organization_id: 'org-1',
      display_name: 'Jane Admin',
      email: 'jane@example.com',
      has_password: true,
      organization_setup_complete: true,
    }),
  ),
]
