import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse } from 'msw'
import { expect, userEvent, within } from 'storybook/test'

import { AuthContext } from '../auth/AuthContext'
import { Onboarding } from './Onboarding'

const meta = {
  title: 'Pages/Onboarding',
  component: Onboarding,
  decorators: [
    (Story) => (
      <AuthContext.Provider
        value={{
          status: 'authenticated',
          role: 'admin',
          organizationId: 'org-1',
          displayName: 'Jane Admin',
          organizationSetupComplete: false,
        }}
      >
        <Story />
      </AuthContext.Provider>
    ),
  ],
  // CORRECTED for the installed msw-storybook-addon@3.0.3's real CSF3 API:
  // v3 dropped `parameters.msw.handlers` in favor of a `beforeEach({ msw })`
  // hook per story (see AdminOverview.stories.tsx for the same pattern).
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
      http.put('*/v1/organizations/me/profile', () => HttpResponse.json({ ok: true })),
    )
  },
} satisfies Meta<typeof Onboarding>

export default meta
type Story = StoryObj<typeof meta>

export const NewAdmin: Story = {
  // Regression test for a bug found in review: tourIndex 0 is a distinct
  // "You're all set" intro screen, and TOUR_STOPS[0..2] ("Filings", "Ask
  // RegRadar", "Organization settings") are tourIndex 1..3 — all three must
  // actually render as "Next" is clicked. (An earlier version folded the
  // intro into tourIndex 0's override of TOUR_STOPS[0], which silently
  // dropped "Filings" from the tour entirely.) Deliberately stops short of
  // clicking "Go to dashboard" on the final screen — that click fires a real
  // `window.location.href` navigation, which breaks the test iframe.
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)

    // Step 1: Welcome
    await userEvent.click(canvas.getByRole('button', { name: 'Get started' }))

    // Step 2: Basics
    await userEvent.type(canvas.getByLabelText('Industry'), 'Biotechnology')
    await userEvent.type(canvas.getByLabelText('Business description'), 'We make diagnostics.')
    await userEvent.click(canvas.getByRole('button', { name: 'Continue' }))

    // Step 3: Watchlist (ChipInput functional)
    await userEvent.type(canvas.getByLabelText('Watchlist entities'), 'Acme Corp{enter}')
    await expect(canvas.getByText('Acme Corp')).toBeInTheDocument()
    await userEvent.type(canvas.getByLabelText('Products'), 'Widget Pro{enter}')
    await userEvent.type(canvas.getByLabelText('Risk priorities'), 'Data privacy{enter}')
    await userEvent.click(canvas.getByRole('button', { name: 'Save and continue' }))

    // Step 4: Done — intro, then all 3 tour stops, each actually reachable.
    await expect(canvas.findByText("You're all set")).resolves.toBeInTheDocument()
    await userEvent.click(canvas.getByRole('button', { name: 'Next' }))
    await expect(canvas.getByText('Filings')).toBeInTheDocument()
    await userEvent.click(canvas.getByRole('button', { name: 'Next' }))
    await expect(canvas.getByText('Ask RegRadar')).toBeInTheDocument()
    await userEvent.click(canvas.getByRole('button', { name: 'Next' }))
    await expect(canvas.getByText('Organization settings')).toBeInTheDocument()
    await expect(canvas.getByRole('button', { name: 'Go to dashboard' })).toBeInTheDocument()
  },
}

export const NonAdminWaiting: Story = {
  decorators: [
    (Story) => (
      <AuthContext.Provider
        value={{
          status: 'authenticated',
          role: 'analyst',
          organizationId: 'org-1',
          displayName: 'Alex Analyst',
          organizationSetupComplete: false,
        }}
      >
        <Story />
      </AuthContext.Provider>
    ),
  ],
}

export const RevisitingWithExistingProfile: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/organizations/me/profile', () =>
        HttpResponse.json({
          industry: 'Biotechnology',
          business_description: 'We manufacture diagnostic devices.',
          watchlist_entities: ['Acme Corp'],
          products: ['Widget Pro'],
          risk_priorities: ['Data privacy'],
          is_complete: true,
        }),
      ),
    )
  },
}
