import type { Meta, StoryObj } from '@storybook/react-vite'

import { Input } from './Input'

const meta = {
  title: 'Components/Input',
  component: Input,
  args: { label: 'Email', placeholder: 'you@example.com' },
} satisfies Meta<typeof Input>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}
export const WithHelperText: Story = { args: { helperText: 'We will never share your email.' } }
export const Focus: Story = {
  args: { autoFocus: true },
  parameters: { docs: { description: { story: 'Focus by tabbing to or clicking the field.' } } },
}
export const Error: Story = { args: { error: 'A valid email address is required.' } }
export const Disabled: Story = { args: { disabled: true, value: 'disabled@example.com' } }
