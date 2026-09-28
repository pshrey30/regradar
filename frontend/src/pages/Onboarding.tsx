import { useEffect, useState, type FormEvent } from 'react'

import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { ChipInput } from '../components/ChipInput'
import { Input } from '../components/Input'
import { useAuth } from '../auth/useAuth'
import { ApiError, apiFetch } from '../lib/api'

interface OrganizationProfileResponse {
  industry: string | null
  business_description: string | null
  watchlist_entities: string[]
  products: string[]
  risk_priorities: string[]
  is_complete: boolean
}

type Step = 'welcome' | 'basics' | 'watchlist' | 'done'
const STEP_ORDER: Step[] = ['welcome', 'basics', 'watchlist', 'done']

function ProgressIndicator({ step }: { step: Step }) {
  const index = STEP_ORDER.indexOf(step)
  return (
    <div className="mb-6 flex gap-1.5">
      {STEP_ORDER.map((s, i) => (
        <div
          key={s}
          className={`h-1 flex-1 rounded-full ${i <= index ? 'bg-primary-600' : 'bg-slate-200'}`}
        />
      ))}
    </div>
  )
}

export function Onboarding() {
  const { role } = useAuth()
  const [step, setStep] = useState<Step>('welcome')
  const [tourIndex, setTourIndex] = useState(0)
  const [industry, setIndustry] = useState('')
  const [businessDescription, setBusinessDescription] = useState('')
  const [watchlistEntities, setWatchlistEntities] = useState<string[]>([])
  const [products, setProducts] = useState<string[]>([])
  const [riskPriorities, setRiskPriorities] = useState<string[]>([])
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
        if (profile.watchlist_entities.length > 0) setWatchlistEntities(profile.watchlist_entities)
        if (profile.products.length > 0) setProducts(profile.products)
        if (profile.risk_priorities.length > 0) setRiskPriorities(profile.risk_priorities)
      })
      .catch(() => {
        // Don't crash on a failed background read — same posture as the
        // rest of this page.
      })
    return () => {
      cancelled = true
    }
  }, [role])

  // tourIndex 0 is the "You're all set" intro screen; tourIndex 1..N map to
  // TOUR_STOPS[0..N-1]. Total screens = TOUR_STOPS.length + 1 (the intro).
  const tourComplete = step === 'done' && tourIndex >= TOUR_STOPS.length + 1

  useEffect(() => {
    if (!tourComplete) return
    // Forces AuthProvider's /v1/me to re-run so organizationSetupComplete
    // flips before ProtectedRoute evaluates again — the same full-reload
    // convention Login.tsx already uses after any auth-state-changing action.
    window.location.href = '/overview'
  }, [tourComplete])

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
          watchlist_entities: watchlistEntities,
          products,
          risk_priorities: riskPriorities,
        }),
      })
      setStep('done')
      setSubmitting(false)
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
      setSubmitting(false)
    }
  }

  return (
    <div className="mx-auto max-w-lg p-8">
      <Card>
        {step !== 'done' && <ProgressIndicator step={step} />}

        {step === 'welcome' && (
          <div className="flex flex-col gap-4">
            <h1 className="text-xl font-semibold text-slate-900">Welcome to RegRadar</h1>
            <p className="text-sm text-slate-600">
              RegRadar watches regulatory filings and scores each one by how much it actually
              matters to your business — not just how severe it is in general.
            </p>
            <p className="text-sm text-slate-600">
              To do that well, we need a bit of context about your organization: your industry,
              the entities and products you care about, and what kinds of risk matter most to
              you. This takes about two minutes.
            </p>
            <Button variant="primary" size="lg" onClick={() => setStep('basics')}>
              Get started
            </Button>
          </div>
        )}

        {step === 'basics' && (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              setStep('watchlist')
            }}
            className="flex flex-col gap-3"
          >
            <h1 className="mb-1 text-xl font-semibold text-slate-900">Tell us about your business</h1>
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
            <div className="mt-2 flex gap-2">
              <Button type="button" variant="secondary" onClick={() => setStep('welcome')}>
                Back
              </Button>
              <Button type="submit" variant="primary" className="flex-1">
                Continue
              </Button>
            </div>
          </form>
        )}

        {step === 'watchlist' && (
          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <h1 className="mb-1 text-xl font-semibold text-slate-900">What should we watch for?</h1>
            <p className="mb-2 text-sm text-slate-500">
              This is what RegRadar uses to score how much a filing actually matters to your
              business.
            </p>
            <ChipInput
              label="Watchlist entities"
              required
              value={watchlistEntities}
              onChange={setWatchlistEntities}
              placeholder="Acme Corp, Example Inc"
            />
            <ChipInput
              label="Products"
              required
              value={products}
              onChange={setProducts}
              placeholder="Widget Pro, Widget Lite"
            />
            <ChipInput
              label="Risk priorities"
              required
              value={riskPriorities}
              onChange={setRiskPriorities}
              placeholder="Data privacy, Environmental compliance"
            />
            {formError && <p className="text-sm text-risk-critical">{formError}</p>}
            <div className="mt-2 flex gap-2">
              <Button type="button" variant="secondary" onClick={() => setStep('basics')}>
                Back
              </Button>
              <Button type="submit" variant="primary" className="flex-1" loading={submitting}>
                Save and continue
              </Button>
            </div>
          </form>
        )}

        {step === 'done' && tourIndex < TOUR_STOPS.length + 1 && (
          <div className="flex flex-col gap-4">
            <h1 className="text-xl font-semibold text-slate-900">
              {tourIndex === 0 ? "You're all set" : TOUR_STOPS[tourIndex - 1].title}
            </h1>
            <p className="text-sm text-slate-600">
              {tourIndex === 0
                ? "Your organization's profile is saved. Filings will now start getting scored against it. Here's a quick look at where things live."
                : TOUR_STOPS[tourIndex - 1].body}
            </p>
            <Button variant="primary" size="lg" onClick={() => setTourIndex((i) => i + 1)}>
              {tourIndex === TOUR_STOPS.length ? 'Go to dashboard' : 'Next'}
            </Button>
          </div>
        )}
      </Card>
    </div>
  )
}

const TOUR_STOPS = [
  { title: 'Filings', body: 'Every filing scored for your organization lands here, ranked by what actually matters to your business.' },
  { title: 'Ask RegRadar', body: 'Ask a plain-English question about your filings and get an answer grounded in the actual text.' },
  { title: 'Organization settings', body: 'Manage your team, sources, and API keys from the Admin nav any time.' },
]
