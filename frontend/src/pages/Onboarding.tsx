import { useEffect, useState, type FormEvent } from 'react'

import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Input } from '../components/Input'
import { useAuth } from '../auth/useAuth'
import { ApiError, apiFetch } from '../lib/api'

function toList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
}

function toCommaString(list: string[]): string {
  return list.join(', ')
}

interface OrganizationProfileResponse {
  industry: string | null
  business_description: string | null
  watchlist_entities: string[]
  products: string[]
  risk_priorities: string[]
  is_complete: boolean
}

export function Onboarding() {
  const { role } = useAuth()
  const [industry, setIndustry] = useState('')
  const [businessDescription, setBusinessDescription] = useState('')
  const [watchlistEntities, setWatchlistEntities] = useState('')
  const [products, setProducts] = useState('')
  const [riskPriorities, setRiskPriorities] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // An Admin who navigates back to /onboarding after already completing it
  // (e.g. to make an edit) should see their real saved data, not a blank
  // form — submitting a blank form would overwrite it. Best-effort: if the
  // GET fails, just leave the form blank, same as today.
  useEffect(() => {
    if (role !== 'admin') return
    let cancelled = false
    apiFetch<OrganizationProfileResponse>('/v1/organizations/me/profile')
      .then((profile) => {
        if (cancelled) return
        if (profile.industry) setIndustry(profile.industry)
        if (profile.business_description) setBusinessDescription(profile.business_description)
        if (profile.watchlist_entities.length > 0) {
          setWatchlistEntities(toCommaString(profile.watchlist_entities))
        }
        if (profile.products.length > 0) setProducts(toCommaString(profile.products))
        if (profile.risk_priorities.length > 0) {
          setRiskPriorities(toCommaString(profile.risk_priorities))
        }
      })
      .catch(() => {
        // Don't crash on a failed background read — same posture as the
        // rest of this page.
      })
    return () => {
      cancelled = true
    }
  }, [role])

  if (role !== 'admin') {
    return (
      <div className="mx-auto max-w-lg p-8">
        <Card>
          <h1 className="mb-2 text-lg font-semibold text-slate-900">Almost there</h1>
          <p className="text-sm text-slate-500">
            Your organization&apos;s Admin needs to finish setting up your organization&apos;s
            profile before filings can be scored. Check back soon.
          </p>
        </Card>
      </div>
    )
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    setSubmitting(true)
    try {
      await apiFetch('/v1/organizations/me/profile', {
        method: 'PUT',
        body: JSON.stringify({
          industry,
          business_description: businessDescription,
          watchlist_entities: toList(watchlistEntities),
          products: toList(products),
          risk_priorities: toList(riskPriorities),
        }),
      })
      // Forces AuthProvider's /v1/me to re-run so organizationSetupComplete
      // flips before ProtectedRoute evaluates again — the same full-reload
      // convention Login.tsx already uses after any auth-state-changing action.
      window.location.href = '/filings'
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
      setSubmitting(false)
    }
  }

  return (
    <div className="mx-auto max-w-lg p-8">
      <Card>
        <h1 className="mb-1 text-xl font-semibold text-slate-900">Tell us about your organization</h1>
        <p className="mb-6 text-sm text-slate-500">
          This is what RegRadar uses to score how much a filing actually matters to your
          business — not just how severe it is.
        </p>
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <Input
            label="Industry"
            required
            value={industry}
            onChange={(e) => setIndustry(e.target.value)}
            placeholder="e.g. Biotechnology"
          />
          <div className="flex flex-col gap-1.5">
            <label htmlFor="business-description" className="text-sm font-medium text-slate-900">
              Business description
            </label>
            <textarea
              id="business-description"
              required
              value={businessDescription}
              onChange={(e) => setBusinessDescription(e.target.value)}
              className="min-h-24 rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-600"
              placeholder="What does your organization do?"
            />
          </div>
          <Input
            label="Watchlist entities (comma-separated)"
            required
            value={watchlistEntities}
            onChange={(e) => setWatchlistEntities(e.target.value)}
            placeholder="Acme Corp, Example Inc"
          />
          <Input
            label="Products (comma-separated)"
            required
            value={products}
            onChange={(e) => setProducts(e.target.value)}
            placeholder="Widget Pro, Widget Lite"
          />
          <Input
            label="Risk priorities (comma-separated)"
            required
            value={riskPriorities}
            onChange={(e) => setRiskPriorities(e.target.value)}
            placeholder="Data privacy, Environmental compliance"
          />
          {formError && <p className="text-sm text-risk-critical">{formError}</p>}
          <Button type="submit" variant="primary" size="lg" className="w-full" loading={submitting}>
            Save and continue
          </Button>
        </form>
      </Card>
    </div>
  )
}
