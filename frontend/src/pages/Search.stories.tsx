import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { expect, userEvent, waitFor, within } from 'storybook/test'

import { Search } from './Search'

// Real field names confirmed from Search.tsx itself:
//   SearchSource: filing_id, excerpt, entity_name
//   SearchResponse: answer (string | null), sources (SearchSource[]), degraded (boolean)
//   mutation: POST /v1/filings/search { query } -> SearchResponse — fires only on
//     form submit, there is no query-on-mount here (unlike every other page so far).
//
// The search input is queried via its accessible label
// ("Ask a question about past filings", set through aria-label on <Input>), and
// submission is driven by clicking the "Ask" button (type="submit") rather than
// pressing Enter, to exercise the real <form onSubmit> handler.
//
// Search doesn't call useAuth(), so no AuthContext is needed — but it does render
// <Link to={`/filings/:id`}> for each source, so a MemoryRouter decorator is
// required (same reasoning as ApiKeys.stories.tsx not needing it, and
// Activity.stories.tsx needing it for its own Links).

const meta = {
  title: 'Pages/Search',
  component: Search,
  decorators: [
    (Story) => (
      <MemoryRouter initialEntries={['/search']}>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof Search>

export default meta
type Story = StoryObj<typeof meta>

// No MSW handler for the search endpoint here: nothing in this story triggers a
// submission, so registering one would never be exercised.
export const Initial: Story = {}

export const WithResults: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.post('*/v1/filings/search', () =>
        HttpResponse.json({
          answer: 'The SEC has flagged late 10-K filings as a recurring compliance concern.',
          sources: [
            {
              filing_id: 'f-101',
              entity_name: 'Acme Corp',
              excerpt: 'Acme Corp filed its 10-K three weeks after the statutory deadline.',
            },
          ],
          degraded: false,
        }),
      ),
    )
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)

    const input = canvas.getByLabelText('Ask a question about past filings')
    await userEvent.type(input, 'What has the SEC said about late 10-K filings?')
    await userEvent.click(canvas.getByRole('button', { name: 'Ask' }))

    await waitFor(() =>
      expect(
        canvas.getByText(
          'The SEC has flagged late 10-K filings as a recurring compliance concern.',
        ),
      ).toBeInTheDocument(),
    )
    expect(canvas.getByText('Acme Corp')).toBeInTheDocument()
    expect(
      canvas.getByText('Acme Corp filed its 10-K three weeks after the statutory deadline.'),
    ).toBeInTheDocument()
  },
}

export const ErrorState: Story = {
  beforeEach({ msw }) {
    msw.use(http.post('*/v1/filings/search', () => new HttpResponse(null, { status: 500 })))
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)

    const input = canvas.getByLabelText('Ask a question about past filings')
    await userEvent.type(input, 'What has the SEC said about late 10-K filings?')
    await userEvent.click(canvas.getByRole('button', { name: 'Ask' }))

    // apiFetch throws an ApiError for any non-2xx response; with a null (non-JSON)
    // body it falls back to `Request to ${path} failed with ${status}` (see
    // lib/api.ts), which the component renders verbatim since the error is an
    // instanceof ApiError.
    await waitFor(() =>
      expect(
        canvas.getByText('Request to /v1/filings/search failed with 500'),
      ).toBeInTheDocument(),
    )
  },
}
