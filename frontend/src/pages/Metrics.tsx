import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { Card } from '../components/Card'
import { MetricCard, type MetricFormat } from '../components/MetricCard'
import { MetricsTrendChart, type TrendPoint } from '../components/MetricsTrendChart'
import { ApiError, apiFetch } from '../lib/api'

interface MetricValue {
  value: number | null
  target: number | null
}

interface MetricsSnapshot {
  id: string
  created_at: string
  ragas_faithfulness: MetricValue
  ragas_context_recall: MetricValue
  rouge_l: MetricValue
  alert_precision: MetricValue
  alert_recall: MetricValue
  p99_latency_ms: MetricValue
  avg_cost_per_filing_usd: MetricValue
}

const TREND_WINDOW_DAYS = 30

const CARD_DEFINITIONS: {
  key: keyof Pick<
    MetricsSnapshot,
    | 'ragas_faithfulness'
    | 'ragas_context_recall'
    | 'rouge_l'
    | 'alert_precision'
    | 'alert_recall'
    | 'p99_latency_ms'
    | 'avg_cost_per_filing_usd'
  >
  label: string
  format: MetricFormat
  higherIsBetter: boolean
}[] = [
  { key: 'ragas_faithfulness', label: 'Faithfulness', format: 'percent', higherIsBetter: true },
  { key: 'ragas_context_recall', label: 'Context Recall', format: 'percent', higherIsBetter: true },
  { key: 'rouge_l', label: 'ROUGE-L', format: 'percent', higherIsBetter: true },
  { key: 'alert_precision', label: 'Alert Precision', format: 'percent', higherIsBetter: true },
  { key: 'alert_recall', label: 'Alert Recall', format: 'percent', higherIsBetter: true },
  { key: 'p99_latency_ms', label: 'P99 Latency', format: 'ms-as-minutes', higherIsBetter: false },
  { key: 'avg_cost_per_filing_usd', label: 'Cost per Filing', format: 'usd', higherIsBetter: false },
]

export function Metrics() {
  const latestQuery = useQuery({
    queryKey: ['metrics', 'latest'],
    queryFn: () => apiFetch<MetricsSnapshot>('/v1/metrics'),
    retry: (failureCount, error) =>
      // A 404 here means "no eval run recorded yet", not a transient
      // failure — retrying it just delays showing the real empty state.
      !(error instanceof ApiError && error.status === 404) && failureCount < 3,
  })

  // Computed once per mount (not on every render — Date.now() is impure)
  // via useState's lazy initializer.
  const [since] = useState(() =>
    new Date(Date.now() - TREND_WINDOW_DAYS * 24 * 60 * 60 * 1000).toISOString(),
  )
  const trendQuery = useQuery({
    queryKey: ['metrics', 'trend'],
    queryFn: () => apiFetch<MetricsSnapshot[]>(`/v1/metrics?since=${encodeURIComponent(since)}`),
  })

  const trend = trendQuery.data ?? []
  const trendPoints: TrendPoint[] = trend.map((run) => ({
    date: run.created_at,
    faithfulness: run.ragas_faithfulness.value,
    cost: run.avg_cost_per_filing_usd.value,
  }))
  const previousSnapshot = trend.length >= 2 ? trend[trend.length - 2] : null

  const noDataYet =
    latestQuery.isError &&
    latestQuery.error instanceof ApiError &&
    latestQuery.error.status === 404

  const forbidden =
    latestQuery.isError &&
    latestQuery.error instanceof ApiError &&
    latestQuery.error.status === 403

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold text-slate-900">Metrics & Cost</h1>

      {latestQuery.isPending && (
        <Card>
          <p className="text-sm text-slate-500">Loading…</p>
        </Card>
      )}

      {noDataYet && (
        <Card>
          <p className="text-sm text-slate-500">
            No eval run has been recorded yet. Metrics appear here once the eval harness runs at
            least once (CI or <code className="font-mono text-xs">regradar run-eval</code>).
          </p>
        </Card>
      )}

      {forbidden && (
        <Card>
          <p className="text-sm text-slate-500">You don&rsquo;t have permission to view metrics.</p>
        </Card>
      )}

      {latestQuery.isError && !noDataYet && !forbidden && (
        <Card>
          <p className="text-sm text-risk-critical">
            {latestQuery.error instanceof ApiError
              ? latestQuery.error.message
              : 'Something went wrong loading metrics.'}
          </p>
        </Card>
      )}

      {latestQuery.isSuccess && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {CARD_DEFINITIONS.map((def) => (
            <MetricCard
              key={def.key}
              label={def.label}
              format={def.format}
              higherIsBetter={def.higherIsBetter}
              value={latestQuery.data[def.key].value}
              target={latestQuery.data[def.key].target}
              previousValue={previousSnapshot ? previousSnapshot[def.key].value : null}
            />
          ))}
        </div>
      )}

      <Card>
        <p className="mb-1 text-sm font-semibold text-slate-900">
          Trailing {TREND_WINDOW_DAYS} days
        </p>
        {trendQuery.isPending ? (
          <p className="py-8 text-center text-sm text-slate-500">Loading…</p>
        ) : trendQuery.isError ? (
          <p className="py-8 text-center text-sm text-risk-critical">
            {trendQuery.error instanceof ApiError
              ? trendQuery.error.message
              : 'Something went wrong loading the trend.'}
          </p>
        ) : (
          <MetricsTrendChart points={trendPoints} />
        )}
      </Card>
    </div>
  )
}
