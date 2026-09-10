import type { Meta, StoryObj } from '@storybook/react-vite'

import { MetricsTrendChart } from './MetricsTrendChart'

const meta = {
  title: 'Components/MetricsTrendChart',
  component: MetricsTrendChart,
} satisfies Meta<typeof MetricsTrendChart>

export default meta
type Story = StoryObj<typeof meta>

export const WithData: Story = {
  args: {
    points: [
      { date: '2026-08-01', faithfulness: 0.86, cost: 0.021 },
      { date: '2026-08-08', faithfulness: 0.88, cost: 0.019 },
      { date: '2026-08-15', faithfulness: 0.85, cost: 0.023 },
      { date: '2026-08-22', faithfulness: 0.91, cost: 0.018 },
      { date: '2026-08-29', faithfulness: 0.9, cost: 0.017 },
    ],
  },
}

export const NotEnoughData: Story = {
  args: { points: [{ date: '2026-08-29', faithfulness: 0.9, cost: null }] },
}

export const OneSeriesOnly: Story = {
  args: {
    points: [
      { date: '2026-08-01', faithfulness: 0.86, cost: null },
      { date: '2026-08-08', faithfulness: 0.88, cost: null },
      { date: '2026-08-15', faithfulness: 0.85, cost: null },
    ],
  },
}
