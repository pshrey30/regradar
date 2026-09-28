import type { Meta, StoryObj } from '@storybook/react-vite'

import { RadarScene } from './RadarScene'

const meta = {
  title: 'Components/RadarScene',
  component: RadarScene,
} satisfies Meta<typeof RadarScene>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}
