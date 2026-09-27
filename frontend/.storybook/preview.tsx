import type { Preview } from '@storybook/react-vite'
import { initialize, mswLoader } from 'msw-storybook-addon'

import '../src/index.css'
import { setOnUnauthorized } from '../src/lib/api'
import { handlers } from '../src/mocks/handlers'

// A story that exercises a 401/error MSW response must not trigger the
// real app's redirect side effect — Storybook has no router history to
// redirect within, and the real callback would throw.
setOnUnauthorized(() => {})

initialize({ onUnhandledRequest: 'bypass' })

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

    msw: {
      handlers,
    },
  },

  loaders: [mswLoader],
};

export default preview;
