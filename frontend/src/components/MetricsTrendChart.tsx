export interface TrendPoint {
  date: string
  faithfulness: number | null
  cost: number | null
}

const WIDTH = 600
const HEIGHT = 160
const PADDING = 8

// A hand-rolled SVG line chart rather than pulling in a charting library —
// this renders exactly two independently-scaled series over a fixed
// window, which a few dozen lines of SVG covers without the bundle
// weight (and dependency-update surface) of recharts/chart.js for
// something this narrow in scope.
function buildPath(values: (number | null)[]): string | null {
  const points = values
    .map((value, index) => ({ value, index }))
    .filter((p): p is { value: number; index: number } => p.value !== null)

  if (points.length < 2) return null

  const min = Math.min(...points.map((p) => p.value))
  const max = Math.max(...points.map((p) => p.value))
  const range = max - min || 1
  const xStep = (WIDTH - PADDING * 2) / Math.max(values.length - 1, 1)

  return points
    .map((p, i) => {
      const x = PADDING + p.index * xStep
      const y = HEIGHT - PADDING - ((p.value - min) / range) * (HEIGHT - PADDING * 2)
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

export function MetricsTrendChart({ points }: { points: TrendPoint[] }) {
  const faithfulnessPath = buildPath(points.map((p) => p.faithfulness))
  const costPath = buildPath(points.map((p) => p.cost))

  return (
    <div>
      <div className="mb-3 flex items-center gap-4 text-xs text-slate-600">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-primary-600" />
          Faithfulness
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-risk-medium" />
          Cost per filing
        </span>
        <span className="text-slate-400">(each independently scaled — trend shape, not shared axis)</span>
      </div>
      {faithfulnessPath === null && costPath === null ? (
        <p className="py-8 text-center text-sm text-slate-500">
          Not enough data yet to show a trend — needs at least two eval runs in this window.
        </p>
      ) : (
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" role="img" aria-label="Metrics trend chart">
          {faithfulnessPath && (
            <path d={faithfulnessPath} fill="none" stroke="#4F46E5" strokeWidth={2} />
          )}
          {costPath && <path d={costPath} fill="none" stroke="#D97706" strokeWidth={2} />}
        </svg>
      )}
    </div>
  )
}
