import type { Meta, StoryObj } from '@storybook/react-vite'
import { useState } from 'react'

import { Button } from './Button'
import { Modal } from './Modal'

// Modal is controlled (isOpen/onClose) rather than self-managing state, so
// every story needs a small stateful wrapper to actually show it open —
// rendering <Modal isOpen={false} .../> directly would just render null.
function ModalDemo({ title, children }: { title?: string; children: React.ReactNode }) {
  const [isOpen, setIsOpen] = useState(true)
  return (
    <>
      <Button onClick={() => setIsOpen(true)}>Reopen modal</Button>
      <Modal isOpen={isOpen} onClose={() => setIsOpen(false)} title={title}>
        {children}
      </Modal>
    </>
  )
}

const meta = {
  title: 'Components/Modal',
  component: ModalDemo,
} satisfies Meta<typeof ModalDemo>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    title: 'Confirm action',
    children: <p className="text-sm text-slate-600">Are you sure you want to continue?</p>,
  },
}

export const WithoutTitle: Story = {
  args: { children: <p className="text-sm text-slate-600">A modal with no title bar.</p> },
}
