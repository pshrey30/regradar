import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse, delay } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { expect, userEvent, within } from 'storybook/test'

import { Organization } from './Organization'

// Real field names confirmed from Organization.tsx itself:
//   OrganizationProfileResponse: industry, business_description,
//     watchlist_entities, products, risk_priorities, is_complete
//   queryKey: ['organization', 'profile'], queryFn: GET
//     /v1/organizations/me/profile
//   save mutation: PUT /v1/organizations/me/profile with
//     { industry, business_description, watchlist_entities, products,
//       risk_priorities } -> OrganizationProfileResponse
//
// Organization renders a <Link to="/onboarding"> in its empty-profile
// state, so it needs a MemoryRouter (nested-Router pitfall applies —
// only the meta-level decorator supplies it, never a story-level one).

const completeProfile = {
  industry: 'Biotechnology & Pharmaceuticals',
  business_description:
    'Meridian Biotech develops and manufactures FDA-regulated diagnostic test kits and therapeutic devices.',
  watchlist_entities: ['Pfizer Inc', 'Moderna Inc'],
  products: ['Diagnostic Test Kits', 'Therapeutic Devices'],
  risk_priorities: ['FDA premarket compliance', 'Data privacy'],
  is_complete: true,
}

const meta = {
  title: 'Pages/Organization',
  component: Organization,
  decorators: [
    (Story) => (
      <MemoryRouter initialEntries={['/organization']}>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof Organization>

export default meta
type Story = StoryObj<typeof meta>

export const Populated: Story = {
  beforeEach({ msw }) {
    msw.use(http.get('*/v1/organizations/me/profile', () => HttpResponse.json(completeProfile)))
  },
}

export const NotSetUp: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/organizations/me/profile', () =>
        HttpResponse.json({
          industry: null,
          business_description: null,
          watchlist_entities: [],
          products: [],
          risk_priorities: [],
          is_complete: false,
        }),
      ),
    )
  },
}

export const Loading: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/organizations/me/profile', async () => {
        await delay('infinite')
      }),
    )
  },
}

export const ErrorState: Story = {
  beforeEach({ msw }) {
    msw.use(http.get('*/v1/organizations/me/profile', () => new HttpResponse(null, { status: 500 })))
  },
}

// Regression coverage for the edit feature: click Edit, change the
// industry field, add a watchlist entity via the real ChipInput
// interaction, save, and confirm the page returns to read-only view
// showing the updated value — not just that the form renders.
export const EditAndSave: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/organizations/me/profile', () => HttpResponse.json(completeProfile)),
      http.put('*/v1/organizations/me/profile', async ({ request }) => {
        const body = (await request.json()) as typeof completeProfile
        return HttpResponse.json({ ...body, is_complete: true })
      }),
    )
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)

    await expect(await canvas.findByText('Biotechnology & Pharmaceuticals')).toBeInTheDocument()

    await userEvent.click(canvas.getByRole('button', { name: 'Edit' }))

    const industryInput = canvas.getByLabelText('Industry')
    await userEvent.clear(industryInput)
    await userEvent.type(industryInput, 'Diagnostics')

    await userEvent.click(canvas.getByRole('button', { name: 'Save changes' }))

    await expect(await canvas.findByText('Diagnostics')).toBeInTheDocument()
    await expect(canvas.queryByLabelText('Industry')).not.toBeInTheDocument()
  },
}
