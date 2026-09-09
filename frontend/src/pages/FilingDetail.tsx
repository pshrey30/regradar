import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { Badge, type DomainValue, type RiskLevel } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { API_BASE_URL, ApiError, apiFetch } from '../lib/api'

interface Obligation {
  description: string
  source_citation?: string | null
}

interface Deadline {
  description: string
  date?: string | null
}

interface Extraction {
  obligations: Obligation[] | null
  deadlines: Deadline[] | null
  risk_flags: string[] | null
  affected_products: string[] | null
  key_entities: string[] | null
  competitor_mentions: string[] | null
}

interface SimilarFiling {
  id: string
  entity_name: string
  filing_type: string
  published_at: string
}

interface FilingDetailResponse {
  id: string
  entity_name: string
  filing_type: string
  domain: DomainValue | null
  risk_level: RiskLevel | null
  priority_score: number | null
  published_at: string
  status: string
  brief: { executive_brief: string } | null
  similar_filings: SimilarFiling[]
  // Absent entirely (not merely null) for the Executive role — see
  // api/routers/filings.py's get_filing.
  extraction?: Extraction | null
}

interface PersonaBriefResponse {
  persona: string
  summary: string
}

type PersonaTab = 'executive' | 'cco' | 'analyst' | 'engineer'

const PERSONA_TABS: { key: PersonaTab; label: string }[] = [
  { key: 'executive', label: 'Executive' },
  { key: 'cco', label: 'CCO' },
  { key: 'analyst', label: 'Analyst' },
  { key: 'engineer', label: 'Engineer' },
]

const STATUS_LABELS: Record<string, string> = {
  ingested: 'Ingested',
  classifying: 'Classifying',
  needs_classification: 'Needs classification',
  needs_review: 'Needs review',
  retrieving: 'Retrieving context',
  analyzing: 'Analyzing',
  summarizing: 'Summarizing',
  delivering: 'Delivering',
  complete: 'Complete',
  failed: 'Failed',
}

// FE-04's ticket calls for Supabase Realtime, but this deployment's
// Postgres is local Docker, not a real Supabase project (see migration
// 0016's docstring) — this derives our own backend's WebSocket URL from
// the same API_BASE_URL every other request already uses.
function statusWebSocketUrl(filingId: string): string {
  const wsBase = API_BASE_URL.replace(/^http/, 'ws')
  return `${wsBase}/v1/filings/${filingId}/status/ws`
}

function PersonaBrief({ filingId, persona }: { filingId: string; persona: PersonaTab }) {
  const query = useQuery({
    queryKey: ['filing-brief', filingId, persona],
    queryFn: () =>
      apiFetch<PersonaBriefResponse>(
        `/v1/filings/${filingId}/brief${persona === 'executive' ? '' : `?persona=${persona}`}`,
      ),
  })

  if (query.isPending) {
    return <div className="h-4 w-2/3 animate-pulse rounded bg-slate-200" />
  }
  if (query.isError) {
    return (
      <p className="text-sm text-risk-critical">
        {query.error instanceof ApiError ? query.error.message : 'Could not load this brief.'}
      </p>
    )
  }
  return <p className="whitespace-pre-line text-sm text-slate-700">{query.data?.summary}</p>
}

function ObligationsAndDeadlines({ extraction }: { extraction: Extraction }) {
  const obligations = extraction.obligations ?? []
  const deadlines = extraction.deadlines ?? []

  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-900">Obligations</h3>
        {obligations.length === 0 ? (
          <p className="text-sm text-slate-500">No obligations extracted.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {obligations.map((obligation, index) => (
              <li key={index} className="rounded-md border border-slate-200 p-3 text-sm">
                <p className="text-slate-900">{obligation.description}</p>
                {obligation.source_citation && (
                  <p className="mt-1 font-mono text-xs text-slate-500">
                    {obligation.source_citation}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-900">Deadlines</h3>
        {deadlines.length === 0 ? (
          <p className="text-sm text-slate-500">No deadlines extracted.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {deadlines.map((deadline, index) => (
              <li
                key={index}
                className="flex items-center justify-between rounded-md border border-slate-200 p-3 text-sm"
              >
                <span className="text-slate-900">{deadline.description}</span>
                {deadline.date && <span className="font-mono text-xs text-slate-500">{deadline.date}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export function FilingDetail() {
  const { filingId } = useParams<{ filingId: string }>()
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<PersonaTab>('executive')
  const [liveStatus, setLiveStatus] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['filing', filingId],
    queryFn: () => apiFetch<FilingDetailResponse>(`/v1/filings/${filingId}`),
    enabled: !!filingId,
  })

  // Live-updating status: a WebSocket backed by a real Postgres
  // LISTEN/NOTIFY trigger (migration 0016), not Supabase Realtime — see
  // that migration's docstring for why. Reconnects if the connection
  // drops for any reason other than an intentional unmount.
  useEffect(() => {
    if (!filingId) return
    let cancelled = false
    let socket: WebSocket | null = null

    function connect() {
      if (cancelled) return
      socket = new WebSocket(statusWebSocketUrl(filingId as string))
      socket.onmessage = (event) => {
        const payload = JSON.parse(event.data) as { status: string }
        setLiveStatus((previous) => {
          if (previous !== null && previous !== payload.status) {
            queryClient.invalidateQueries({ queryKey: ['filing', filingId] })
          }
          return payload.status
        })
      }
      socket.onclose = () => {
        if (!cancelled) setTimeout(connect, 2000)
      }
    }
    connect()

    return () => {
      cancelled = true
      socket?.close()
    }
  }, [filingId, queryClient])

  async function handleViewOriginal() {
    if (!filingId) return
    try {
      const { url } = await apiFetch<{ url: string }>(`/v1/filings/${filingId}/pdf-url`)
      window.open(url, '_blank', 'noopener,noreferrer')
    } catch {
      // A 404 (no stored PDF yet) or transient failure — nothing to open;
      // the button staying clickable for a retry is enough feedback here.
    }
  }

  // isPending (not isLoading — isLoading is `isPending && isFetching`,
  // which briefly goes false during a retry's backoff delay even though
  // there's still no data and no settled error) is the correct "we have
  // nothing to show yet" check.
  if (query.isPending) {
    return <Card>Loading…</Card>
  }
  if (query.isError) {
    return (
      <Card>
        <p className="text-sm text-risk-critical">
          {query.error instanceof ApiError ? query.error.message : 'Could not load this filing.'}
        </p>
      </Card>
    )
  }

  const filing = query.data
  if (!filing) return null

  const displayedStatus = liveStatus ?? filing.status

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">{filing.entity_name}</h1>
            <p className="text-sm text-slate-600">{filing.filing_type}</p>
          </div>
          <div className="flex items-center gap-2">
            {filing.domain && <Badge variant="domain" value={filing.domain} />}
            {filing.risk_level && <Badge variant="risk" value={filing.risk_level} />}
            <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium capitalize text-slate-600">
              {STATUS_LABELS[displayedStatus] ?? displayedStatus}
            </span>
          </div>
        </div>
        <div className="mt-4 flex flex-col gap-3 text-sm text-slate-500 sm:flex-row sm:flex-wrap sm:items-center sm:gap-4">
          <div className="flex flex-wrap gap-4">
            <span>Published {new Date(filing.published_at).toLocaleDateString()}</span>
            {filing.priority_score !== null && (
              <span>Priority score: {filing.priority_score.toFixed(2)}</span>
            )}
          </div>
          <Button
            variant="secondary"
            size="sm"
            className="w-full whitespace-nowrap sm:w-auto"
            onClick={handleViewOriginal}
          >
            View Original Document
          </Button>
        </div>
      </Card>

      <Card>
        <div className="mb-4 flex gap-1 border-b border-slate-200">
          {PERSONA_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => setActiveTab(tab.key)}
              className={[
                'border-b-2 px-3 py-2 text-sm font-medium',
                activeTab === tab.key
                  ? 'border-primary-600 text-primary-600'
                  : 'border-transparent text-slate-500 hover:text-slate-900',
              ].join(' ')}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <PersonaBrief filingId={filing.id} persona={activeTab} />
      </Card>

      {filing.extraction !== undefined && (
        <Card>
          {filing.extraction === null ? (
            <p className="text-sm text-slate-500">No extraction is available for this filing yet.</p>
          ) : (
            <ObligationsAndDeadlines extraction={filing.extraction} />
          )}
        </Card>
      )}

      {filing.similar_filings.length > 0 && (
        <Card>
          <h3 className="mb-3 text-sm font-semibold text-slate-900">Similar Filings</h3>
          <div className="flex flex-col gap-2">
            {filing.similar_filings.map((similar) => (
              <Link
                key={similar.id}
                to={`/filings/${similar.id}`}
                className="rounded-md border border-slate-200 p-3 text-sm hover:bg-slate-50"
              >
                <span className="font-medium text-slate-900">{similar.entity_name}</span>
                <span className="ml-2 text-slate-500">{similar.filing_type}</span>
                <span className="ml-2 text-xs text-slate-400">
                  {new Date(similar.published_at).toLocaleDateString()}
                </span>
              </Link>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}
