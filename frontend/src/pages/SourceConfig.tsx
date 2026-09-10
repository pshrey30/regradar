import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { ApiError, apiFetch } from '../lib/api'

type Source = 'SEC' | 'FDA' | 'FINRA'
type Domain = 'financial' | 'clinical' | 'environmental' | 'other'

interface SourceConfigItem {
  source: Source
  domains: string[]
  is_active: boolean
  poll_interval_seconds: number
  last_polled_at: string | null
}

const SOURCES: Source[] = ['SEC', 'FDA', 'FINRA']
const DOMAINS: { value: Domain; label: string }[] = [
  { value: 'financial', label: 'Financial' },
  { value: 'clinical', label: 'Clinical' },
  { value: 'environmental', label: 'Environmental' },
  { value: 'other', label: 'Other' },
]

// The backend tracks one domain set per source, but there's no product
// reason for a monitored domain to differ by regulator — the toggle UI
// exposes a single shared set and applies it to every active source, which
// keeps the staged-changes model in this screen simple.
function domainsFromRows(rows: SourceConfigItem[]): Set<Domain> {
  const set = new Set<Domain>()
  for (const row of rows) {
    for (const domain of row.domains) set.add(domain as Domain)
  }
  return set
}

export function SourceConfig() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['config', 'sources'],
    queryFn: () => apiFetch<SourceConfigItem[]>('/v1/config/sources'),
  })

  const [activeSources, setActiveSources] = useState<Set<Source>>(new Set())
  const [activeDomains, setActiveDomains] = useState<Set<Domain>>(new Set())
  const [saveError, setSaveError] = useState<string | null>(null)
  const [savedJustNow, setSavedJustNow] = useState(false)

  // Re-stage from the server whenever fresh data arrives (initial load, or
  // after a successful save) — never while the user has unsaved edits
  // in-flight, so a background refetch can't clobber them. Adjusting state
  // during render (React's documented pattern for this, rather than an
  // effect) avoids the extra commit-then-rerender flash a `useEffect`
  // sync would cause.
  const [syncedFrom, setSyncedFrom] = useState<SourceConfigItem[] | undefined>(undefined)
  if (query.data !== undefined && query.data !== syncedFrom) {
    setSyncedFrom(query.data)
    setActiveSources(new Set(query.data.filter((row) => row.is_active).map((row) => row.source)))
    setActiveDomains(domainsFromRows(query.data))
  }

  const dirty =
    query.data !== undefined &&
    (!setsEqual(activeSources, new Set(query.data.filter((row) => row.is_active).map((row) => row.source))) ||
      !setsEqual(activeDomains, domainsFromRows(query.data)))

  const saveMutation = useMutation({
    mutationFn: () =>
      apiFetch<SourceConfigItem[]>('/v1/config/sources', {
        method: 'POST',
        body: JSON.stringify({
          sources: Array.from(activeSources),
          domains: Array.from(activeDomains),
        }),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(['config', 'sources'], data)
      setSaveError(null)
      setSavedJustNow(true)
      setTimeout(() => setSavedJustNow(false), 3000)
    },
    onError: (error) => {
      setSaveError(
        error instanceof ApiError ? error.message : 'Something went wrong saving your changes.',
      )
    },
  })

  function toggleSource(source: Source) {
    setSaveError(null)
    setActiveSources((prev) => {
      const next = new Set(prev)
      if (next.has(source)) next.delete(source)
      else next.add(source)
      return next
    })
  }

  function toggleDomain(domain: Domain) {
    setSaveError(null)
    setActiveDomains((prev) => {
      const next = new Set(prev)
      if (next.has(domain)) next.delete(domain)
      else next.add(domain)
      return next
    })
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Source Configuration</h1>
        {savedJustNow && <span className="text-sm text-risk-low-text">Saved</span>}
      </div>

      {query.isPending && (
        <Card>
          <p className="text-sm text-slate-500">Loading…</p>
        </Card>
      )}

      {query.isError && (
        <Card>
          <p className="text-sm text-risk-critical">
            {query.error instanceof ApiError
              ? query.error.message
              : 'Something went wrong loading source configuration.'}
          </p>
        </Card>
      )}

      {query.isSuccess && (
        <>
          <Card>
            <p className="mb-3 text-sm font-semibold text-slate-900">Regulators</p>
            <div className="flex flex-col gap-2">
              {SOURCES.map((source) => (
                <ToggleRow
                  key={source}
                  label={source}
                  checked={activeSources.has(source)}
                  onChange={() => toggleSource(source)}
                />
              ))}
            </div>
          </Card>

          <Card>
            <p className="mb-3 text-sm font-semibold text-slate-900">Domains</p>
            <div className="flex flex-col gap-2">
              {DOMAINS.map((domain) => (
                <ToggleRow
                  key={domain.value}
                  label={domain.label}
                  checked={activeDomains.has(domain.value)}
                  onChange={() => toggleDomain(domain.value)}
                />
              ))}
            </div>
          </Card>

          {saveError && (
            <Card>
              <p className="text-sm text-risk-critical">{saveError}</p>
            </Card>
          )}

          <div className="flex justify-end">
            <Button
              onClick={() => saveMutation.mutate()}
              disabled={!dirty}
              loading={saveMutation.isPending}
            >
              Save Changes
            </Button>
          </div>
        </>
      )}
    </div>
  )
}

function setsEqual<T>(a: Set<T>, b: Set<T>): boolean {
  if (a.size !== b.size) return false
  for (const item of a) if (!b.has(item)) return false
  return true
}

function ToggleRow({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: () => void
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between rounded-md px-3 py-2 hover:bg-slate-50">
      <span className="text-sm text-slate-700">{label}</span>
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        className="h-4 w-4 rounded border-slate-300 text-primary-600 focus:ring-primary-600"
      />
    </label>
  )
}
