import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse } from 'msw'

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

export const NewAdmin: Story = {}

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
