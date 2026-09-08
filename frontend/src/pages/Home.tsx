import { useAuth } from '../auth/useAuth'
import { Card } from '../components/Card'

// A real Filings List (FE-03) isn't built yet — this placeholder exists
// so FE-02's "land on the Filings List with a valid session" acceptance
// criterion has somewhere real to land, without building FE-03 early.
export function Home() {
  const { role, displayName } = useAuth()

  return (
    <Card>
      <h1 className="mb-2 text-xl font-semibold text-slate-900">Welcome, {displayName}</h1>
      <p className="text-sm text-slate-600">
        Signed in as <span className="font-medium capitalize">{role?.replace('_', ' ')}</span>. The
        Filings List (FE-03) isn't built yet — this is where it will live.
      </p>
    </Card>
  )
}
