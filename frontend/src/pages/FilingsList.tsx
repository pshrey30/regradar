import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { allowedDomainsForRole } from '../auth/domainScope'
import { useAuth } from '../auth/useAuth'
import { Badge, type DomainValue, type RiskLevel } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Pagination } from '../components/Pagination'
import { Table, type TableColumn } from '../components/Table'
import { useLiveFilingStatus } from '../hooks/useLiveFilingStatus'
import { ApiError, apiFetch } from '../lib/api'
import { FILING_STATUS_LABELS } from '../lib/filingStatus'

// Kept small enough that a full page of rows fits on screen without the
// table itself needing to scroll before the Pagination controls come into
// view — the whole page should scroll, never a nested section.
const PAGE_SIZE = 10

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

const ALL_DOMAIN_OPTIONS: DomainValue[] = [
  'financial',
  'clinical',
  'environmental',
  'engineering',
  'other',
]
const RISK_OPTIONS: RiskLevel[] = ['low', 'medium', 'high', 'critical']
const SOURCE_OPTIONS = ['SEC', 'FDA', 'FINRA'] as const

// The exact set of URL query params this screen owns — kept in the URL
// (not component state) so a filtered view is bookmarkable/shareable, per
// the ticket's own acceptance criteria.
const FILTER_KEYS = ['domain', 'risk', 'source', 'since', 'until'] as const
type FilterKey = (typeof FILTER_KEYS)[number]

function buildFilingsQuery(
  filters: Record<FilterKey, string>,
  page: number,
  pageSize: number,
): string {
  const params = new URLSearchParams()
  for (const key of FILTER_KEYS) {
    if (filters[key]) params.set(key, filters[key])
  }
  params.set('page', String(page))
  params.set('page_size', String(pageSize))
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

interface PendingFilingsResponse {
  data: PendingFilingItem[]
  page: number
  page_size: number
  total: number
}

const PENDING_PAGE_SIZE = 10

// Distinct colors per pending state so an Admin can tell "needs a human
// decision" (needs_review) apart from "the classifier never ran"
// (needs_classification) at a glance, instead of both reading as the same
// generic amber "in progress" — they need different follow-up actions.
// The mid-pipeline states (retrieving/analyzing/summarizing/delivering)
// share one "actively working" color rather than each getting their own,
// since none of them needs a distinct follow-up action the way
// needs_review/needs_classification do.
const _STATUS_COLORS: Record<string, string> = {
  failed: 'border-risk-critical text-risk-critical',
  needs_review: 'border-risk-high text-risk-high-text',
  needs_classification: 'border-primary-600 text-primary-700',
  needs_organization_setup: 'border-risk-medium text-risk-medium-text',
  classifying: 'border-primary-600 text-primary-700',
  retrieving: 'border-primary-600 text-primary-700',
  analyzing: 'border-primary-600 text-primary-700',
  summarizing: 'border-primary-600 text-primary-700',
  delivering: 'border-primary-600 text-primary-700',
}
const _DEFAULT_STATUS_COLOR = 'border-slate-400 text-slate-600'
// Pipeline stages worth a small pulsing dot — this is what makes "the
// pipeline is actively working on this filing right now" visible at a
// glance, distinct from a static/stuck state like needs_review.
const _ACTIVE_STATUSES = new Set([
  'classifying',
  'retrieving',
  'analyzing',
  'summarizing',
  'delivering',
])

function PendingStatusBadge({ status }: { status: string }) {
  const isActive = _ACTIVE_STATUSES.has(status)
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${
        _STATUS_COLORS[status] ?? _DEFAULT_STATUS_COLOR
      }`}
    >
      {isActive && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary-600" />}
      {FILING_STATUS_LABELS[status] ?? status}
    </span>
  )
}

// One row of the pending-processing panel below. Its own status/ws
// connection (the same one FilingDetail.tsx uses) makes each stage of the
// pipeline visible live, pixel by pixel, as it happens — REST's `status`
// prop is only the last-known value from the panel's own poll/refetch, so
// the live value (once the socket delivers one) always wins.
function PendingFilingRow({
  filing,
  isProcessing,
  disabled,
  onProcessNow,
  onLiveStatusChange,
}: {
  filing: PendingFilingItem
  isProcessing: boolean
  disabled: boolean
  onProcessNow: () => void
  onLiveStatusChange: () => void
}) {
  const liveStatus = useLiveFilingStatus(filing.id, onLiveStatusChange)
  const displayedStatus = liveStatus ?? filing.status

  return (
    <div className="flex items-center justify-between gap-3 py-2">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate font-medium text-slate-900">{filing.entity_name}</span>
          <span className="text-sm text-slate-500">{filing.filing_type}</span>
          <PendingStatusBadge status={displayedStatus} />
        </div>
        {displayedStatus === 'failed' && filing.processing_error && (
          <p className="mt-0.5 truncate text-xs text-risk-critical" title={filing.processing_error}>
            {filing.processing_error}
          </p>
        )}
      </div>
      <Button variant="secondary" size="sm" loading={isProcessing} disabled={disabled} onClick={onProcessNow}>
        Process now
      </Button>
    </div>
  )
}

// GET /v1/filings only ever returns status=complete filings (it inner-joins
// briefs, which don't exist until the pipeline finishes) — so a filing
// stuck earlier in the pipeline is invisible there by construction. This
// panel is the only place in the UI that surfaces it, via the separate
// admin-only /v1/filings/pending endpoint. Ingestion never auto-triggers
// processing by itself, but "Fetch now" (SourceConfig.tsx) does hand every
// pending filing to the pipeline in the background after fetching — this
// panel's own "Process now" per row remains for manual retries/re-runs.
function PendingFilingsPanel() {
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const query = useQuery({
    queryKey: ['filings-pending', page],
    queryFn: () =>
      apiFetch<PendingFilingsResponse>(
        `/v1/filings/pending?page=${page}&page_size=${PENDING_PAGE_SIZE}`,
      ),
    // Short enough that a filing "Fetch now" just created (or a Process
    // now that just started elsewhere) shows up here without a manual
    // refresh — each row's own status/ws is what makes the pipeline
    // stages inside it live; this interval is only for the pending *set*
    // itself changing (new filings arriving, complete ones dropping off).
    refetchInterval: 5000,
  })

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ['filings-pending'] })
    queryClient.invalidateQueries({ queryKey: ['filings'] })
  }

  const processMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ id: string; status: string }>(`/v1/filings/${id}/process`, { method: 'POST' }),
    onSuccess: invalidate,
  })

  if (query.isPending || query.isError || (query.data?.total ?? 0) === 0) {
    return null
  }

  return (
    <Card>
      <div className="flex flex-col gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">
            Pending processing ({query.data.total})
          </h2>
          <p className="text-sm text-slate-500">
            Ingested but not yet summarized or delivered — run the pipeline manually below, or
            with <code className="rounded bg-slate-100 px-1 py-0.5">regradar process-pending</code>.
          </p>
        </div>
        <div className="flex flex-col divide-y divide-slate-200">
          {query.data.data.map((filing) => (
            <PendingFilingRow
              key={filing.id}
              filing={filing}
              isProcessing={processMutation.isPending && processMutation.variables === filing.id}
              disabled={processMutation.isPending}
              onProcessNow={() => processMutation.mutate(filing.id)}
              // A filing reaching "complete" drops off this endpoint
              // entirely (see this function's own docstring) — invalidating
              // on every live status change is what removes its row (or
              // picks up a fresh processing_error once it's "failed").
              onLiveStatusChange={invalidate}
            />
          ))}
        </div>
        <Pagination
          page={query.data.page}
          pageSize={query.data.page_size}
          total={query.data.total}
          onPageChange={setPage}
        />
      </div>
    </Card>
  )
}

export function FilingsList() {
  const { role } = useAuth()
  const allowedDomains = allowedDomainsForRole(role)
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()

  const filters = Object.fromEntries(
    FILTER_KEYS.map((key) => [key, searchParams.get(key) ?? '']),
  ) as Record<FilterKey, string>
  const page = Number(searchParams.get('page') ?? '1') || 1

  function updateFilter(key: FilterKey, value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    // Any filter change invalidates the current page position.
    next.delete('page')
    setSearchParams(next)
  }

  function updatePage(nextPage: number) {
    const next = new URLSearchParams(searchParams)
    next.set('page', String(nextPage))
    setSearchParams(next)
  }

  const query = useQuery({
    queryKey: ['filings', filters, page],
    queryFn: () =>
      apiFetch<FilingListResponse>(`/v1/filings?${buildFilingsQuery(filters, page, PAGE_SIZE)}`),
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

      {allowedDomains && (
        <p className="-mt-2 text-sm text-slate-500">
          Showing {allowedDomains.map((d) => d[0].toUpperCase() + d.slice(1)).join(' and ')}{' '}
          filings only — scoped to your role.
        </p>
      )}

      {role === 'admin' && <PendingFilingsPanel />}

      <Card>
        <div className="flex flex-wrap items-end gap-3">
          {/* A role locked to a single domain has nothing to filter — the
              banner above already says what it's scoped to, so the
              dropdown would just be one option offering no real choice. */}
          {(allowedDomains === null || allowedDomains.length > 1) && (
            <FilterSelect
              label="Domain"
              value={filters.domain}
              options={allowedDomains ?? ALL_DOMAIN_OPTIONS}
              onChange={(v) => updateFilter('domain', v)}
            />
          )}
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
        <>
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
          {query.data && (
            <Pagination
              page={query.data.page}
              pageSize={query.data.page_size}
              total={query.data.total}
              onPageChange={updatePage}
            />
          )}
        </>
      )}
    </div>
  )
}
