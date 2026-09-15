import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
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

interface PendingFilingItem {
  id: string
  entity_name: string
  filing_type: string
  source: string
  status: string
  ingested_at: string
  processing_error: string | null
}

const _STATUS_LABELS: Record<string, string> = {
  ingested: 'Ingested',
  classifying: 'Classifying',
  needs_classification: 'Needs classification',
  needs_review: 'Needs review',
  retrieving: 'Retrieving',
  analyzing: 'Analyzing',
  summarizing: 'Summarizing',
  delivering: 'Delivering',
  failed: 'Failed',
}

function PendingStatusBadge({ status }: { status: string }) {
  const isFailed = status === 'failed'
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${
        isFailed
          ? 'border-risk-critical text-risk-critical'
          : 'border-risk-medium text-risk-medium'
      }`}
    >
      {_STATUS_LABELS[status] ?? status}
    </span>
  )
}

// GET /v1/filings only ever returns status=complete filings (it inner-joins
// briefs, which don't exist until the pipeline finishes) — so a filing
// stuck earlier in the pipeline is invisible there by construction. This
// panel is the only place in the UI that surfaces it, via the separate
// admin-only /v1/filings/pending endpoint. Ingestion never auto-triggers
// processing (a deliberate, cost-gated choice — see the CLI's
// process-pending command); this is where an admin acts on that manually.
function PendingFilingsPanel() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['filings-pending'],
    queryFn: () => apiFetch<{ data: PendingFilingItem[] }>('/v1/filings/pending'),
  })

  const processMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ id: string; status: string }>(`/v1/filings/${id}/process`, { method: 'POST' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['filings-pending'] })
      queryClient.invalidateQueries({ queryKey: ['filings'] })
    },
  })

  if (query.isPending || query.isError || (query.data?.data.length ?? 0) === 0) {
    return null
  }

  return (
    <Card>
      <div className="flex flex-col gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">
            Pending processing ({query.data.data.length})
          </h2>
          <p className="text-sm text-slate-500">
            Ingested but not yet summarized or delivered — run the pipeline manually below, or
            with <code className="rounded bg-slate-100 px-1 py-0.5">regradar process-pending</code>.
          </p>
        </div>
        <div className="flex flex-col divide-y divide-slate-200">
          {query.data.data.map((filing) => {
            const isProcessingThis =
              processMutation.isPending && processMutation.variables === filing.id
            return (
              <div key={filing.id} className="flex items-center justify-between gap-3 py-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium text-slate-900">
                      {filing.entity_name}
                    </span>
                    <span className="text-sm text-slate-500">{filing.filing_type}</span>
                    <PendingStatusBadge status={filing.status} />
                  </div>
                  {filing.status === 'failed' && filing.processing_error && (
                    <p className="mt-0.5 truncate text-xs text-risk-critical" title={filing.processing_error}>
                      {filing.processing_error}
                    </p>
                  )}
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  loading={isProcessingThis}
                  disabled={processMutation.isPending}
                  onClick={() => processMutation.mutate(filing.id)}
                >
                  Process now
                </Button>
              </div>
            )
          })}
        </div>
      </div>
    </Card>
  )
}

export function FilingsList() {
  const { role } = useAuth()
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

      {role === 'admin' && <PendingFilingsPanel />}

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
