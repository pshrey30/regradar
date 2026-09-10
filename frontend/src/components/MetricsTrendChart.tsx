export interface TrendPoint {
  date: string
  faithfulness: number | null
  cost: number | null
}

const WIDTH = 600
const HEIGHT = 160
const PADDING = 8

interface PlottedPoint {
  x: number
  y: number
  index: number
  value: number
}

// A hand-rolled SVG line chart rather than pulling in a charting library —
// this renders exactly two independently-scaled series over a fixed
// window, which a few dozen lines of SVG covers without the bundle
// weight (and dependency-update surface) of recharts/chart.js for
// something this narrow in scope.
function buildPlot(values: (number | null)[]): { path: string; points: PlottedPoint[] } | null {
  const points = values
    .map((value, index) => ({ value, index }))
    .filter((p): p is { value: number; index: number } => p.value !== null)

  if (points.length < 2) return null

  const min = Math.min(...points.map((p) => p.value))
  const max = Math.max(...points.map((p) => p.value))
  const range = max - min || 1
  const xStep = (WIDTH - PADDING * 2) / Math.max(values.length - 1, 1)

  const plotted = points.map((p) => ({
    x: PADDING + p.index * xStep,
    y: HEIGHT - PADDING - ((p.value - min) / range) * (HEIGHT - PADDING * 2),
    index: p.index,
    value: p.value,
  }))

  const path = plotted.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')

  return { path, points: plotted }
}

function formatDate(date: string): string {
  return new Date(date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

// Markers carry a native <title> so hovering (or, for touch/assistive
// tech, focusing) any point on either line shows its exact date + value —
// the line shape alone only communicates trend direction, not readable
// values.
function Markers({
  plot,
  color,
  formatValue,
  dates,
}: {
  plot: { points: PlottedPoint[] } | null
  color: string
  formatValue: (value: number) => string
  dates: string[]
}) {
  if (!plot) return null
  return (
    <>
      {plot.points.map((p) => (
        <circle key={p.index} cx={p.x} cy={p.y} r={4} fill={color} tabIndex={0} className="cursor-pointer">
          <title>
            {formatDate(dates[p.index])}: {formatValue(p.value)}
          </title>
        </circle>
      ))}
    </>
  )
}

export function MetricsTrendChart({ points }: { points: TrendPoint[] }) {
  const dates = points.map((p) => p.date)
  const faithfulnessPlot = buildPlot(points.map((p) => p.faithfulness))
  const costPlot = buildPlot(points.map((p) => p.cost))

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
      {faithfulnessPlot === null && costPlot === null ? (
        <p className="py-8 text-center text-sm text-slate-500">
          Not enough data yet to show a trend — needs at least two eval runs in this window.
        </p>
      ) : (
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" role="img" aria-label="Metrics trend chart">
          {faithfulnessPlot && (
            <path d={faithfulnessPlot.path} fill="none" stroke="#4F46E5" strokeWidth={2} />
          )}
          {costPlot && <path d={costPlot.path} fill="none" stroke="#D97706" strokeWidth={2} />}
          <Markers
            plot={faithfulnessPlot}
            color="#4F46E5"
            dates={dates}
            formatValue={(v) => `${(v * 100).toFixed(1)}%`}
          />
          <Markers
            plot={costPlot}
            color="#D97706"
            dates={dates}
            formatValue={(v) => `$${v.toFixed(3)}`}
          />
        </svg>
      )}
    </div>
  )
}
