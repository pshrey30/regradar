import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router-dom'

import { AuthContext } from '../auth/AuthContext'
import { AdminShell } from './AdminShell'

const meta = {
  title: 'Components/AdminShell',
  component: AdminShell,
  decorators: [
    (Story) => (
      <MemoryRouter initialEntries={['/overview']}>
        <AuthContext.Provider
          value={{
            status: 'authenticated',
            role: 'admin',
            organizationId: 'org-1',
            displayName: 'Jane Admin',
            organizationSetupComplete: true,
          }}
        >
          <Story />
        </AuthContext.Provider>
      </MemoryRouter>
    ),
  ],
  args: { children: <div className="text-sm text-slate-500">Page content goes here</div> },
} satisfies Meta<typeof AdminShell>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}
