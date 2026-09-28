import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'

import { Login } from './Login'

const meta = {
  title: 'Pages/Login',
  component: Login,
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof Login>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  async beforeEach({ msw }) {
    msw.use(
      http.post('*/v1/auth/login', () => new HttpResponse(null, { status: 200 })),
      http.post('*/v1/auth/signup', () => new HttpResponse(null, { status: 201 })),
      http.post('*/v1/auth/signup-org', () => new HttpResponse(null, { status: 201 })),
    )
  },
}
