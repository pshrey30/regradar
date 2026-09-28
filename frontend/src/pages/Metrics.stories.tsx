import type { Meta, StoryObj } from '@storybook/react-vite'
import { http, HttpResponse, delay } from 'msw'

import type { JsonBodyType } from 'msw'

import { Metrics } from './Metrics'

// Real field names confirmed from Metrics.tsx itself:
//   MetricValue: value (number | null), target (number | null)
//   MetricsSnapshot: id, created_at, ragas_faithfulness, ragas_context_recall,
//     rouge_l, alert_precision, alert_recall, p99_latency_ms,
//     avg_cost_per_filing_usd (all MetricValue)
//   FunnelStatusCount: status, count
//   FunnelResponse: data (FunnelStatusCount[]), total
//
// Three independent queries:
//   ['metrics', 'funnel'] -> GET /v1/metrics/funnel -> FunnelResponse
//     (IngestionFunnel renders null on pending/error/total===0 — failures
//     here are invisible, by design)
//   ['metrics', 'latest'] -> GET /v1/metrics -> MetricsSnapshot
//     (404 means "no eval run yet" and shows a dedicated empty-state Card,
//     not the generic error Card; 403 shows a permission Card)
//   ['metrics', 'trend'] -> GET /v1/metrics?since=... -> MetricsSnapshot[]
//     (same path as 'latest' — the /v1/metrics handler below branches on
//     the `since` query param to serve the right shape to each query)
//
// Metrics.tsx doesn't call useAuth() or render any <Link> — no AuthContext
// or Router decorator needed here (unlike FilingsList/Activity).

const latestSnapshot = {
  id: 'run-42',
  created_at: '2026-09-27T06:00:00Z',
  ragas_faithfulness: { value: 0.91, target: 0.87 },
  ragas_context_recall: { value: 0.88, target: 0.85 },
  rouge_l: { value: 0.76, target: 0.7 },
  alert_precision: { value: 0.94, target: 0.9 },
  alert_recall: { value: 0.89, target: 0.85 },
  p99_latency_ms: { value: 120_000, target: 180_000 },
  avg_cost_per_filing_usd: { value: 0.018, target: 0.025 },
}

const trendSnapshots = [
  {
    id: 'run-38',
    created_at: '2026-08-30T06:00:00Z',
    ragas_faithfulness: { value: 0.86, target: 0.87 },
    ragas_context_recall: { value: 0.83, target: 0.85 },
    rouge_l: { value: 0.72, target: 0.7 },
    alert_precision: { value: 0.9, target: 0.9 },
    alert_recall: { value: 0.86, target: 0.85 },
    p99_latency_ms: { value: 150_000, target: 180_000 },
    avg_cost_per_filing_usd: { value: 0.021, target: 0.025 },
  },
  {
    id: 'run-40',
    created_at: '2026-09-13T06:00:00Z',
    ragas_faithfulness: { value: 0.88, target: 0.87 },
    ragas_context_recall: { value: 0.85, target: 0.85 },
    rouge_l: { value: 0.74, target: 0.7 },
    alert_precision: { value: 0.92, target: 0.9 },
    alert_recall: { value: 0.87, target: 0.85 },
    p99_latency_ms: { value: 135_000, target: 180_000 },
    avg_cost_per_filing_usd: { value: 0.019, target: 0.025 },
  },
  latestSnapshot,
]

const funnelData = {
  data: [
    { status: 'ingested', count: 3 },
    { status: 'analyzing', count: 2 },
    { status: 'needs_review', count: 1 },
    { status: 'complete', count: 48 },
    { status: 'failed', count: 2 },
  ],
  total: 56,
}

// The 'latest' and 'trend' queries both hit GET /v1/metrics — they differ
// only by the `since` query param, so one handler branches on it.
function metricsHandler(payload: { latest: JsonBodyType; trend: JsonBodyType }) {
  return http.get('*/v1/metrics', ({ request }) => {
    const url = new URL(request.url)
    if (url.searchParams.has('since')) {
      return HttpResponse.json(payload.trend)
    }
    return HttpResponse.json(payload.latest)
  })
}

const meta = {
  title: 'Pages/Metrics',
  component: Metrics,
} satisfies Meta<typeof Metrics>

export default meta
type Story = StoryObj<typeof meta>

export const Populated: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/metrics/funnel', () => HttpResponse.json(funnelData)),
      metricsHandler({ latest: latestSnapshot, trend: trendSnapshots }),
    )
  },
}

export const Empty: Story = {
  beforeEach({ msw }) {
    msw.use(
      // total: 0 -> IngestionFunnel renders null.
      http.get('*/v1/metrics/funnel', () => HttpResponse.json({ data: [], total: 0 })),
      // 404 on 'latest' -> dedicated "no eval run yet" Card; empty trend
      // array -> MetricsTrendChart's own "not enough data" state.
      http.get('*/v1/metrics', ({ request }) => {
        const url = new URL(request.url)
        if (url.searchParams.has('since')) {
          return HttpResponse.json([])
        }
        return HttpResponse.json(
          { error: { code: 'not_found', message: 'No eval run has been recorded yet.' } },
          { status: 404 },
        )
      }),
    )
  },
}

export const Loading: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('*/v1/metrics/funnel', async () => {
        await delay('infinite')
      }),
      http.get('*/v1/metrics', async () => {
        await delay('infinite')
      }),
    )
  },
}

export const ErrorState: Story = {
  beforeEach({ msw }) {
    msw.use(
      // Funnel errors are invisible by design (component returns null), so
      // this exercises the two queries that do surface errors: the generic
      // error Card for 'latest' (a non-404/403 failure) and the trend
      // chart's inline error state.
      http.get('*/v1/metrics/funnel', () => new HttpResponse(null, { status: 500 })),
      http.get('*/v1/metrics', () => new HttpResponse(null, { status: 500 })),
    )
  },
}
