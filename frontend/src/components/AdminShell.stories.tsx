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

// Content taller than the viewport must scroll on its own inside <main> —
// the sidebar's nav links and its signed-in-as/Sign out block stay fixed
// on screen the whole time, never scrolling away with a long Activity feed
// or Filings table. See DashboardChrome.tsx's own comment on this.
export const TallContent: Story = {
  args: {
    children: (
      <div className="flex flex-col gap-4">
        {Array.from({ length: 40 }, (_, i) => (
          <div key={i} className="rounded-md border border-slate-200 p-4 text-sm text-slate-600">
            Row {i + 1}
          </div>
        ))}
      </div>
    ),
  },
}
