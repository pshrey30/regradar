import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { Card } from '../components/Card'
import { ApiError, apiFetch } from '../lib/api'

interface OrganizationProfileResponse {
  industry: string | null
  business_description: string | null
  watchlist_entities: string[]
  products: string[]
  risk_priorities: string[]
  is_complete: boolean
}

function ChipList({ items }: { items: string[] }) {
  if (items.length === 0) return <p className="text-sm text-slate-400">None set.</p>
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <span
          key={item}
          className="inline-flex items-center rounded-full bg-primary-50 px-2.5 py-1 text-xs font-medium text-primary-700"
        >
          {item}
        </span>
      ))}
    </div>
  )
}

// Admin-only (see App.tsx's RESTRICTED_PATHS) — one place that shows every
// piece of the organization's profile together, rather than it being
// scattered across Onboarding (write-only, first-run) and the pieces
// AdminOverview surfaces individually. Read-only for now: editing an
// existing org profile after onboarding is out of scope here (revisiting
// /onboarding already covers that — its own effect pre-fills the form
// from this same GET endpoint).
export function Organization() {
  const query = useQuery({
    queryKey: ['organization', 'profile'],
    queryFn: () => apiFetch<OrganizationProfileResponse>('/v1/organizations/me/profile'),
  })

  return (
    <div className="flex max-w-2xl flex-col gap-4">
      <h1 className="text-xl font-semibold text-slate-900">Organization</h1>
      <p className="-mt-2 text-sm text-slate-500">
        The profile RegRadar uses to score how much each filing matters to your business.
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

      {query.isSuccess && !query.data.is_complete && (
        <Card>
          <p className="text-sm text-slate-500">
            Your organization&rsquo;s profile isn&rsquo;t set up yet.{' '}
            <Link to="/onboarding" className="font-medium text-primary-600 hover:underline">
              Finish onboarding
            </Link>{' '}
            to fill this in.
          </p>
        </Card>
      )}

      {query.isSuccess && query.data.is_complete && (
        <>
          <Card>
            <h2 className="mb-3 text-sm font-semibold text-slate-900">Industry</h2>
            <p className="text-sm text-slate-700">{query.data.industry}</p>
          </Card>

          <Card>
            <h2 className="mb-3 text-sm font-semibold text-slate-900">Business description</h2>
            <p className="text-sm text-slate-700">{query.data.business_description}</p>
          </Card>

          <Card>
            <h2 className="mb-3 text-sm font-semibold text-slate-900">Watchlist entities</h2>
            <ChipList items={query.data.watchlist_entities} />
          </Card>

          <Card>
            <h2 className="mb-3 text-sm font-semibold text-slate-900">Products</h2>
            <ChipList items={query.data.products} />
          </Card>

          <Card>
            <h2 className="mb-3 text-sm font-semibold text-slate-900">Risk priorities</h2>
            <ChipList items={query.data.risk_priorities} />
          </Card>

          <p className="text-xs text-slate-400">
            To change any of this, revisit{' '}
            <Link to="/onboarding" className="text-primary-600 hover:underline">
              onboarding
            </Link>
            .
          </p>
        </>
      )}
    </div>
  )
}
