import { useParams } from 'react-router-dom'

import { Card } from '../components/Card'

// FE-04 (the real Filing Detail screen) isn't built yet — this placeholder
// exists so FE-03's "clicking a row navigates to the Filing Detail screen"
// acceptance criterion has somewhere real to land.
export function FilingDetail() {
  const { filingId } = useParams<{ filingId: string }>()

  return (
    <Card>
      <h1 className="mb-2 text-xl font-semibold text-slate-900">Filing Detail</h1>
      <p className="text-sm text-slate-600">
        Filing <span className="font-mono">{filingId}</span> — the real Filing Detail screen
        (FE-04) isn't built yet.
      </p>
    </Card>
  )
}
