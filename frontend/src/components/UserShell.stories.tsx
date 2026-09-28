import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router-dom'

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

export const Default: Story = {}

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
}
