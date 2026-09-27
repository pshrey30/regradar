import { http, HttpResponse } from 'msw'

// Every handler here is a REASONABLE DEFAULT for a story that doesn't
// care about this endpoint's exact response — individual stories
// override via their own `parameters.msw.handlers` array (MSW's
// per-story override convention), which takes precedence over these.
// Wildcard base (`*/v1/...`) so this works regardless of how
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
