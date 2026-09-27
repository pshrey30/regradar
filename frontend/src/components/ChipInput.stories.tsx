import type { Meta, StoryObj } from '@storybook/react-vite'
import { expect, userEvent, within } from 'storybook/test'
import { useState } from 'react'

import { ChipInput, type ChipInputProps } from './ChipInput'

function Controlled(props: Omit<ChipInputProps, 'value' | 'onChange'> & { initial: string[] }) {
  const [value, setValue] = useState(props.initial)
  return <ChipInput {...props} value={value} onChange={setValue} />
}

const meta = {
  title: 'Components/ChipInput',
  component: Controlled,
  args: { label: 'Watchlist entities', placeholder: 'Type a name and press Enter…', initial: [] },
} satisfies Meta<typeof Controlled>

export default meta
type Story = StoryObj<typeof meta>

export const Empty: Story = {}

export const Populated: Story = {
  args: { initial: ['Acme Corp', 'Example Inc'] },
}

export const AddAndRemove: Story = {
  args: { initial: ['Acme Corp'] },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    // Query by role rather than placeholder text: the component hides its
    // placeholder once at least one chip exists (this story starts with one).
    const input = canvas.getByRole('textbox')

    await userEvent.type(input, 'Beta LLC{enter}')
    await expect(canvas.getByText('Beta LLC')).toBeInTheDocument()

    // Duplicate entry is rejected, not added twice.
    await userEvent.type(input, 'Beta LLC{enter}')
    expect(canvas.getAllByText('Beta LLC')).toHaveLength(1)

    const removeButton = canvas.getByLabelText('Remove Acme Corp')
    await userEvent.click(removeButton)
    expect(canvas.queryByText('Acme Corp')).not.toBeInTheDocument()
  },
}
