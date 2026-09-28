import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router-dom'
import { expect, within } from 'storybook/test'

import { AuthContext } from '../auth/AuthContext'
import { UserShell } from './UserShell'

const meta = {
  title: 'Components/UserShell',
  component: UserShell,
  decorators: [
    (Story) => (
      <MemoryRouter initialEntries={['/filings']}>
        <AuthContext.Provider
          value={{
            status: 'authenticated',
            role: 'analyst',
            organizationId: 'org-1',
            displayName: 'Alex Analyst',
            organizationSetupComplete: true,
          }}
        >
          <Story />
        </AuthContext.Provider>
      </MemoryRouter>
    ),
  ],
  args: { children: <div className="text-sm text-slate-500">Page content goes here</div> },
} satisfies Meta<typeof UserShell>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  play: async ({ canvasElement }) => {
    // analyst: has Search, no Metrics (backend is admin-or-eng_lead only).
    const canvas = within(canvasElement)
    await expect(canvas.getByText('Ask RegRadar')).toBeInTheDocument()
    await expect(canvas.queryByText('Metrics & Cost')).not.toBeInTheDocument()
  },
}

export const AsExecutive: Story = {
  decorators: [
    // Only override the AuthContext value here — the meta-level decorator
    // above already supplies the MemoryRouter, and story-level decorators
    // compose with (rather than replace) meta-level ones. Re-wrapping in
    // another MemoryRouter nests two Routers, which React Router rejects.
    (Story) => (
      <AuthContext.Provider
        value={{
          status: 'authenticated',
          role: 'executive',
          organizationId: 'org-1',
          displayName: 'Erin Executive',
          organizationSetupComplete: true,
        }}
      >
        <Story />
      </AuthContext.Provider>
    ),
  ],
  play: async ({ canvasElement }) => {
    // executive: backend 403s POST /v1/filings/search, so the link must
    // not be shown — a visible-but-broken nav item is worse than none.
    const canvas = within(canvasElement)
    await expect(canvas.queryByText('Ask RegRadar')).not.toBeInTheDocument()
    await expect(canvas.queryByText('Metrics & Cost')).not.toBeInTheDocument()
  },
}

export const AsEngLead: Story = {
  decorators: [
    (Story) => (
      <AuthContext.Provider
        value={{
          status: 'authenticated',
          role: 'eng_lead',
          organizationId: 'org-1',
          displayName: 'Evan EngLead',
          organizationSetupComplete: true,
        }}
      >
        <Story />
      </AuthContext.Provider>
    ),
  ],
  play: async ({ canvasElement }) => {
    // eng_lead: previously had partial-admin access to Metrics (backend
    // is admin-or-eng_lead), and Search is allowed too (backend excludes
    // only executive) — both links must be present.
    const canvas = within(canvasElement)
    await expect(canvas.getByText('Ask RegRadar')).toBeInTheDocument()
    await expect(canvas.getByText('Metrics & Cost')).toBeInTheDocument()
  },
}
