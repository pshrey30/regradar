import type { Meta, StoryObj } from '@storybook/react-vite'

import { Card } from './Card'

const meta = {
  title: 'Components/Card',
  component: Card,
} satisfies Meta<typeof Card>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    children: (
      <>
        <h3 className="mb-2 text-base font-semibold text-slate-900">Card title</h3>
        <p className="text-sm text-slate-600">Card body content goes here.</p>
      </>
    ),
  },
}
