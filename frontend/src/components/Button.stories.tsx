import type { Meta, StoryObj } from '@storybook/react-vite'

import { Button } from './Button'

// hover/focus are interactive pseudo-states demonstrated by interacting with
// any story in the running Storybook UI, not separate frozen stories — this
// project has no pseudo-states addon installed, so that's the honest limit
// of what a static story can represent for those two.
const meta = {
  title: 'Components/Button',
  component: Button,
  args: { children: 'Button' },
} satisfies Meta<typeof Button>

export default meta
type Story = StoryObj<typeof meta>

export const Primary: Story = { args: { variant: 'primary' } }
export const Secondary: Story = { args: { variant: 'secondary' } }
export const Destructive: Story = { args: { variant: 'destructive' } }
export const Ghost: Story = { args: { variant: 'ghost' } }

export const Small: Story = { args: { size: 'sm' } }
export const Medium: Story = { args: { size: 'md' } }
export const Large: Story = { args: { size: 'lg' } }

export const Disabled: Story = { args: { disabled: true } }
export const Loading: Story = { args: { loading: true } }
