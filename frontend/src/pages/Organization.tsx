import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { ChipInput } from '../components/ChipInput'
import { Input } from '../components/Input'
import { ApiError, apiFetch } from '../lib/api'

interface OrganizationProfileResponse {
  industry: string | null
  business_description: string | null
  watchlist_entities: string[]
  products: string[]
  risk_priorities: string[]
  is_complete: boolean
}

interface OrganizationProfileRequest {
  industry: string
  business_description: string
  watchlist_entities: string[]
  products: string[]
  risk_priorities: string[]
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

function emptyFormFrom(data: OrganizationProfileResponse): OrganizationProfileRequest {
  return {
    industry: data.industry ?? '',
    business_description: data.business_description ?? '',
    watchlist_entities: data.watchlist_entities,
    products: data.products,
    risk_priorities: data.risk_priorities,
  }
}

// Admin-only (see App.tsx's RESTRICTED_PATHS) — one place that shows every
// piece of the organization's profile together, rather than it being
// scattered across Onboarding (write-only, first-run) and the pieces
// AdminOverview surfaces individually. Editable in place — PUTs the same
// endpoint/contract Onboarding's watchlist step already uses, so there's
// no separate write path to keep in sync.
export function Organization() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['organization', 'profile'],
    queryFn: () => apiFetch<OrganizationProfileResponse>('/v1/organizations/me/profile'),
  })

  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<OrganizationProfileRequest | null>(null)

  const mutation = useMutation({
    mutationFn: (body: OrganizationProfileRequest) =>
      apiFetch<OrganizationProfileResponse>('/v1/organizations/me/profile', {
        method: 'PUT',
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(['organization', 'profile'], data)
      setEditing(false)
      setForm(null)
    },
  })

  function startEditing() {
    if (query.data) {
      mutation.reset()
      setForm(emptyFormFrom(query.data))
      setEditing(true)
    }
  }

  function cancelEditing() {
    mutation.reset()
    setEditing(false)
    setForm(null)
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (form) mutation.mutate(form)
  }

  const formValid =
    form !== null &&
    form.industry.trim() !== '' &&
    form.business_description.trim() !== '' &&
    form.watchlist_entities.length > 0 &&
    form.products.length > 0 &&
    form.risk_priorities.length > 0

  return (
    <div className="flex max-w-2xl flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Organization</h1>
          <p className="text-sm text-slate-500">
            The profile RegRadar uses to score how much each filing matters to your business.
          </p>
        </div>
        {query.isSuccess && query.data.is_complete && !editing && (
          <Button variant="secondary" size="sm" onClick={startEditing}>
            Edit
          </Button>
        )}
      </div>

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

      {query.isSuccess && query.data.is_complete && !editing && (
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
        </>
      )}

      {editing && form && (
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <Card>
            <Input
              label="Industry"
              required
              value={form.industry}
              onChange={(e) => setForm({ ...form, industry: e.target.value })}
            />
          </Card>

          <Card>
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="org-business-description"
                className="text-sm font-medium text-slate-900"
              >
                Business description
              </label>
              <textarea
                id="org-business-description"
                required
                value={form.business_description}
                onChange={(e) => setForm({ ...form, business_description: e.target.value })}
                className="min-h-24 rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-600"
              />
            </div>
          </Card>

          <Card>
            <ChipInput
              label="Watchlist entities"
              required
              value={form.watchlist_entities}
              onChange={(watchlist_entities) => setForm({ ...form, watchlist_entities })}
            />
          </Card>

          <Card>
            <ChipInput
              label="Products"
              required
              value={form.products}
              onChange={(products) => setForm({ ...form, products })}
            />
          </Card>

          <Card>
            <ChipInput
              label="Risk priorities"
              required
              value={form.risk_priorities}
              onChange={(risk_priorities) => setForm({ ...form, risk_priorities })}
            />
          </Card>

          {mutation.isError && (
            <p className="text-sm text-risk-critical">
              {mutation.error instanceof ApiError
                ? mutation.error.message
                : 'Something went wrong saving these changes.'}
            </p>
          )}

          <div className="flex gap-2">
            <Button type="button" variant="secondary" onClick={cancelEditing}>
              Cancel
            </Button>
            <Button type="submit" disabled={!formValid} loading={mutation.isPending}>
              Save changes
            </Button>
          </div>
        </form>
      )}
    </div>
  )
}
