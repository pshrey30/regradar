import type { Meta, StoryObj } from '@storybook/react-vite'

import { Badge } from './Badge'
import { Table, type TableColumn } from './Table'

interface FilingRow {
  id: string
  entityName: string
  filingType: string
  riskLevel: 'low' | 'medium' | 'high' | 'critical'
}

const columns: TableColumn<FilingRow>[] = [
  { header: 'Entity', accessor: (row) => row.entityName },
  { header: 'Filing Type', accessor: (row) => row.filingType },
  { header: 'Risk', accessor: (row) => <Badge variant="risk" value={row.riskLevel} /> },
]

const sampleData: FilingRow[] = [
  { id: '1', entityName: 'Acme Corp', filingType: '10-K', riskLevel: 'critical' },
  { id: '2', entityName: 'Beta Inc', filingType: '8-K', riskLevel: 'high' },
  { id: '3', entityName: 'Gamma LLC', filingType: '10-Q', riskLevel: 'low' },
]

const meta = {
  title: 'Components/Table',
  component: Table<FilingRow>,
  args: { columns, getRowKey: (row: FilingRow) => row.id },
} satisfies Meta<typeof Table<FilingRow>>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = { args: { data: sampleData } }
export const Empty: Story = { args: { data: [] } }
export const EmptyWithCustomMessage: Story = {
  args: { data: [], emptyMessage: 'No filings match the current filters.' },
}
export const Loading: Story = { args: { data: [], loading: true } }
export const Clickable: Story = {
  args: { data: sampleData, onRowClick: (row) => console.log(`Clicked ${row.entityName}`) },
}
