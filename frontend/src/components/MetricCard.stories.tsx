import type { Meta, StoryObj } from '@storybook/react-vite'

import { MetricCard } from './MetricCard'

const meta = {
  title: 'Components/MetricCard',
  component: MetricCard,
  args: { label: 'Faithfulness', format: 'percent', higherIsBetter: true },
} satisfies Meta<typeof MetricCard>

export default meta
type Story = StoryObj<typeof meta>

export const Improving: Story = {
  args: { value: 0.91, target: 0.87, previousValue: 0.85 },
}

export const Regressing: Story = {
  args: { value: 0.82, target: 0.87, previousValue: 0.9 },
}

export const NoTrendYet: Story = {
  args: { value: 0.91, target: 0.87, previousValue: null },
}

export const NotMeasured: Story = {
  args: {
    label: 'Cost per filing',
    format: 'usd',
    higherIsBetter: false,
    value: null,
    target: null,
    previousValue: null,
  },
}

export const LatencyImproving: Story = {
  args: {
    label: 'P99 latency',
    format: 'ms-as-minutes',
    higherIsBetter: false,
    value: 120_000,
    target: 180_000,
    previousValue: 150_000,
  },
}
