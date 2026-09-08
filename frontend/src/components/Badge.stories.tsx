import type { Meta, StoryObj } from '@storybook/react-vite'

import { Badge } from './Badge'

const meta = {
  title: 'Components/Badge',
  component: Badge,
} satisfies Meta<typeof Badge>

export default meta
type Story = StoryObj<typeof meta>

export const RiskLow: Story = { args: { variant: 'risk', value: 'low' } }
export const RiskMedium: Story = { args: { variant: 'risk', value: 'medium' } }
export const RiskHigh: Story = { args: { variant: 'risk', value: 'high' } }
export const RiskCritical: Story = { args: { variant: 'risk', value: 'critical' } }

export const DomainFinancial: Story = { args: { variant: 'domain', value: 'financial' } }
export const DomainClinical: Story = { args: { variant: 'domain', value: 'clinical' } }
export const DomainEnvironmental: Story = { args: { variant: 'domain', value: 'environmental' } }
export const DomainOther: Story = { args: { variant: 'domain', value: 'other' } }
