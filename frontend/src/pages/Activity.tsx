import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { allowedDomainsForRole } from '../auth/domainScope'
import { useAuth } from '../auth/useAuth'
import { Badge, type DomainValue, type RiskLevel } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Pagination } from '../components/Pagination'
import { SendAlertModal } from '../components/SendAlertModal'
import { ApiError, apiFetch } from '../lib/api'

// Kept small enough that a full page of alerts fits on screen without the
// feed itself needing to scroll before the Pagination controls come into
// view — the whole page should scroll, never a nested section.
const PAGE_SIZE = 10

type Channel = 'slack' | 'email' | 'webhook'
type Status = 'pending' | 'sent' | 'failed' | 'retrying'

interface ActivityItem {
  id: string
  filing_id: string
  entity_name: string
  filing_type: string
  domain: DomainValue | null
  risk_level: RiskLevel | null
  channel: Channel
  recipient: string
  status: Status
  is_fallback: boolean
  at: string
  error_message: string | null
}

interface ActivityListResponse {
  data: ActivityItem[]
  page: number
  page_size: number
  total: number
}

const CHANNEL_LABELS: Record<Channel, string> = {
  slack: 'Slack',
  email: 'Email',
  webhook: 'Webhook',
}

function StatusDot({ status, errorMessage }: { status: Status; errorMessage: string | null }) {
  const color =
    status === 'sent'
      ? 'bg-risk-low'
      : status === 'failed'
        ? 'bg-risk-critical'
        : 'bg-risk-medium' // pending or retrying

  const label = status === 'sent' ? 'Sent' : status === 'failed' ? 'Failed' : 'Retrying'

  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs text-slate-500"
      // Native title tooltip — lightweight, no extra UI chrome, shows the
      // actual delivery-client failure reason (e.g. "HTTP 500",
      // "TimeoutException: ...") on hover for a Failed row.
      title={status === 'failed' && errorMessage ? errorMessage : undefined}
    >
      <span className={`h-2 w-2 rounded-full ${color}`} />
      {label}
      {status === 'failed' && errorMessage && (
        <svg
          className="h-3.5 w-3.5 text-slate-400"
          viewBox="0 0 20 20"
          fill="currentColor"
          aria-hidden="true"
        >
          <path
            fillRule="evenodd"
            d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
            clipRule="evenodd"
          />
        </svg>
      )}
    </span>
  )
}

export function Activity() {
  const { role } = useAuth()
  const allowedDomains = allowedDomainsForRole(role)
  const [page, setPage] = useState(1)
  const [alertFilingId, setAlertFilingId] = useState<string | null>(null)
  const query = useQuery({
    queryKey: ['activity', page],
    queryFn: () =>
      apiFetch<ActivityListResponse>(`/v1/activity?page=${page}&page_size=${PAGE_SIZE}`),
    // Alerts land continuously as new filings are processed — keep this
    // feed reasonably fresh without the user needing to manually refresh.
    refetchInterval: 30_000,
  })

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold text-slate-900">Activity</h1>
      <p className="-mt-2 text-sm text-slate-500">
        {allowedDomains
          ? `Alerts for ${allowedDomains.map((d) => d[0].toUpperCase() + d.slice(1)).join(' and ')} filings — Slack, email, and webhooks — most recent first.`
          : 'Every alert RegRadar has sent — Slack, email, and webhooks — most recent first.'}
      </p>

      {query.isPending && (
        <Card>
          <p className="text-sm text-slate-500">Loading…</p>
        </Card>
      )}

      {query.isError && (
        <Card>
          <p className="text-sm text-risk-critical">
            {query.error instanceof ApiError ? query.error.message : 'Something went wrong.'}
          </p>
        </Card>
      )}

      {query.isSuccess && query.data.data.length === 0 && (
        <Card>
          <p className="text-sm text-slate-500">
            No alerts have gone out yet. They&rsquo;ll show up here the moment a filing is
            delivered.
          </p>
        </Card>
      )}

      {query.isSuccess && query.data.data.length > 0 && (
        <div className="flex flex-col gap-2">
          {query.data.data.map((item) => (
            <Card key={item.id}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      to={`/filings/${item.filing_id}`}
                      className="font-medium text-slate-900 hover:text-primary-600 hover:underline"
                    >
                      {item.entity_name}
                    </Link>
                    <span className="text-sm text-slate-500">{item.filing_type}</span>
                    {item.domain && <Badge variant="domain" value={item.domain} />}
                    {item.risk_level && <Badge variant="risk" value={item.risk_level} />}
                  </div>
                  <p className="mt-1 text-xs text-slate-400">
                    {CHANNEL_LABELS[item.channel]} to{' '}
                    <span className="font-mono text-slate-500">{item.recipient}</span>
                    {item.is_fallback && ' (fallback)'} · {new Date(item.at).toLocaleString()}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <StatusDot status={item.status} errorMessage={item.error_message} />
                  {role === 'admin' && (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setAlertFilingId(item.filing_id)}
                    >
                      Send alert
                    </Button>
                  )}
                </div>
              </div>
            </Card>
          ))}
          <Pagination
            page={query.data.page}
            pageSize={query.data.page_size}
            total={query.data.total}
            onPageChange={setPage}
          />
        </div>
      )}

      {role === 'admin' && (
        <SendAlertModal
          filingId={alertFilingId ?? ''}
          isOpen={alertFilingId !== null}
          onClose={() => setAlertFilingId(null)}
        />
      )}
    </div>
  )
}
