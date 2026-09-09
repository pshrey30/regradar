import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { Badge, type DomainValue, type RiskLevel } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Table, type TableColumn } from '../components/Table'
import { ApiError, apiFetch } from '../lib/api'

interface FilingListItem {
  id: string
  entity_name: string
  filing_type: string
  domain: DomainValue | null
  risk_level: RiskLevel | null
  published_at: string
  executive_brief: string
}

interface FilingListResponse {
  data: FilingListItem[]
  page: number
  page_size: number
  total: number
}

const DOMAIN_OPTIONS: DomainValue[] = ['financial', 'clinical', 'environmental', 'other']
const RISK_OPTIONS: RiskLevel[] = ['low', 'medium', 'high', 'critical']
const SOURCE_OPTIONS = ['SEC', 'FDA', 'FINRA'] as const

// The exact set of URL query params this screen owns — kept in the URL
// (not component state) so a filtered view is bookmarkable/shareable, per
// the ticket's own acceptance criteria.
const FILTER_KEYS = ['domain', 'risk', 'source', 'since', 'until'] as const
type FilterKey = (typeof FILTER_KEYS)[number]

function buildFilingsQuery(filters: Record<FilterKey, string>): string {
  const params = new URLSearchParams()
  for (const key of FILTER_KEYS) {
    if (filters[key]) params.set(key, filters[key])
  }
  return params.toString()
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: readonly string[]
  onChange: (value: string) => void
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium text-slate-900">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-900 capitalize focus:border-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-600"
      >
        <option value="">All</option>
        {options.map((option) => (
          <option key={option} value={option} className="capitalize">
            {option}
          </option>
        ))}
      </select>
    </div>
  )
}

function DateField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium text-slate-900">{label}</label>
      <input
        type="date"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-900 focus:border-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-600"
      />
    </div>
  )
}

export function FilingsList() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()

  const filters = Object.fromEntries(
    FILTER_KEYS.map((key) => [key, searchParams.get(key) ?? '']),
  ) as Record<FilterKey, string>

  function updateFilter(key: FilterKey, value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    setSearchParams(next)
  }

  const query = useQuery({
    queryKey: ['filings', filters],
    queryFn: () => apiFetch<FilingListResponse>(`/v1/filings?${buildFilingsQuery(filters)}`),
  })

  const columns: TableColumn<FilingListItem>[] = [
    { header: 'Entity', accessor: (row) => row.entity_name },
    { header: 'Filing Type', accessor: (row) => row.filing_type },
    {
      header: 'Domain',
      accessor: (row) => (row.domain ? <Badge variant="domain" value={row.domain} /> : '—'),
    },
    {
      header: 'Risk',
      accessor: (row) => (row.risk_level ? <Badge variant="risk" value={row.risk_level} /> : '—'),
    },
    {
      header: 'Published',
      accessor: (row) => new Date(row.published_at).toLocaleDateString(),
    },
  ]

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold text-slate-900">Filings</h1>

      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <FilterSelect
            label="Domain"
            value={filters.domain}
            options={DOMAIN_OPTIONS}
            onChange={(v) => updateFilter('domain', v)}
          />
          <FilterSelect
            label="Risk"
            value={filters.risk}
            options={RISK_OPTIONS}
            onChange={(v) => updateFilter('risk', v)}
          />
          <FilterSelect
            label="Source"
            value={filters.source}
            options={SOURCE_OPTIONS}
            onChange={(v) => updateFilter('source', v)}
          />
          <DateField label="From" value={filters.since} onChange={(v) => updateFilter('since', v)} />
          <DateField label="To" value={filters.until} onChange={(v) => updateFilter('until', v)} />
        </div>
      </Card>

      {query.isError ? (
        <div className="flex items-center justify-between rounded-lg border border-risk-critical bg-white p-4">
          <p className="text-sm text-risk-critical">
            {query.error instanceof ApiError
              ? query.error.message
              : 'Something went wrong loading filings.'}
          </p>
          <Button variant="secondary" size="sm" onClick={() => query.refetch()}>
            Retry
          </Button>
        </div>
      ) : (
        <Table
          columns={columns}
          data={query.data?.data ?? []}
          getRowKey={(row) => row.id}
          // isPending, not isLoading: isLoading (isPending && isFetching)
          // briefly goes false during a retry's backoff delay even with no
          // data yet, which would flash the empty-state message instead of
          // the loading skeleton.
          loading={query.isPending}
          emptyMessage="No filings match the current filters."
          onRowClick={(row) => navigate(`/filings/${row.id}`)}
        />
      )}
    </div>
  )
}
