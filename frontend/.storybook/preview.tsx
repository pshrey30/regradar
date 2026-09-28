import type { Preview } from '@storybook/react-vite'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { mswLoader } from 'msw-storybook-addon/csf3'

import '../src/index.css'
import { setOnUnauthorized } from '../src/lib/api'
import { handlers } from '../src/mocks/handlers'

// A story that exercises a 401/error MSW response must not trigger the
// real app's redirect side effect — Storybook has no router history to
// redirect within, and the real callback would throw.
setOnUnauthorized(() => {})

const preview: Preview = {
  parameters: {
    controls: {
      matchers: {
       color: /(background|color)$/i,
       date: /Date$/i,
      },
    },

    a11y: {
      // 'todo' - show a11y violations in the test UI only
      // 'error' - fail CI on a11y violations
      // 'off' - skip a11y checks entirely
      test: 'todo'
    },
  },

  loaders: [mswLoader()],

  beforeEach({ msw }) {
    msw.use(...handlers)
  },

  // Pages that call useQuery need their own QueryClient in Storybook —
  // the real app's QueryClient lives in main.tsx, which stories never
  // render. `retry: false` so an ErrorState story fails fast on the first
  // MSW error response instead of retrying for several seconds.
  decorators: [
    (Story) => {
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      return (
        <QueryClientProvider client={queryClient}>
          <Story />
        </QueryClientProvider>
      )
    },
  ],
};

export default preview;
