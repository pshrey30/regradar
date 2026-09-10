import { Card } from './Card'

export type MetricFormat = 'percent' | 'ms-as-minutes' | 'usd'

export interface MetricCardProps {
  label: string
  value: number | null
  target: number | null
  format: MetricFormat
  // Whether a *rising* value is the desirable direction for this metric
  // (true for faithfulness/recall/precision, false for latency/cost) —
  // determines which way the trend arrow gets colored green vs red, not
  // just which way it points.
  higherIsBetter: boolean
  // The previous value in the trend series, if any — null when there's
  // only one data point yet, in which case no trend arrow renders at all
  // rather than a misleading flat one.
  previousValue: number | null
}

function formatValue(value: number | null, format: MetricFormat): string {
  if (value === null) return 'Not measured'
  switch (format) {
    case 'percent':
      return `${(value * 100).toFixed(1)}%`
    case 'ms-as-minutes':
      return `${(value / 60_000).toFixed(1)}m`
    case 'usd':
      return `$${value.toFixed(3)}`
  }
}

function formatTarget(target: number | null, format: MetricFormat, higherIsBetter: boolean): string | null {
  if (target === null) return null
  const comparator = higherIsBetter ? '>' : '<'
  return `Target: ${comparator}${formatValue(target, format)}`
}

function TrendArrow({
  value,
  previousValue,
  higherIsBetter,
}: {
  value: number | null
  previousValue: number | null
  higherIsBetter: boolean
}) {
  if (value === null || previousValue === null || value === previousValue) return null

  const rose = value > previousValue
  const isImprovement = rose === higherIsBetter
  const colorClass = isImprovement ? 'text-risk-low-text' : 'text-risk-critical'

  return (
    <span className={`inline-flex items-center text-sm font-medium ${colorClass}`} aria-hidden="true">
      {rose ? '↑' : '↓'}
    </span>
  )
}

export function MetricCard({
  label,
  value,
  target,
  format,
  higherIsBetter,
  previousValue,
}: MetricCardProps) {
  const targetText = formatTarget(target, format, higherIsBetter)

  return (
    <Card>
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <div className="mt-2 flex items-baseline gap-2">
        <p className="text-2xl font-semibold text-slate-900">{formatValue(value, format)}</p>
        <TrendArrow value={value} previousValue={previousValue} higherIsBetter={higherIsBetter} />
      </div>
      <p className="mt-1 text-xs text-slate-500">{targetText ?? 'No documented target'}</p>
    </Card>
  )
}
