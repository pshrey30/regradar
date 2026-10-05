import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Input } from '../components/Input'
import { ApiError, apiFetch } from '../lib/api'

type Source = 'SEC' | 'FDA' | 'FINRA'
type Domain = 'financial' | 'clinical' | 'environmental' | 'engineering' | 'other'

export interface SourceConfigItem {
  source: Source
  domains: string[]
  is_active: boolean
  poll_interval_seconds: number
  last_polled_at: string | null
  feed_url: string | null
}

// FDA is deliberately absent here — it has no on/off toggle of its own.
// Its active state is derived entirely from whether a feed URL is
// configured (see the dirty/save logic below), and it's polled via its own
// "Poll now" button rather than the shared poll-once/toggle flow.
const TOGGLE_SOURCES: Source[] = ['SEC', 'FINRA']
const DOMAINS: { value: Domain; label: string }[] = [
  { value: 'financial', label: 'Financial' },
  { value: 'clinical', label: 'Clinical' },
  { value: 'environmental', label: 'Environmental' },
  { value: 'engineering', label: 'Engineering' },
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

// Polling here is manual (regradar poll-once — there's no always-on
// scheduler), so "stale" can't mean "missed its cadence" the way it
// would for an automated scheduler. This instead flags a source that's
// gone at least 3x its own configured interval without a poll — a loose
// enough bar to not nag over a normal gap between manual runs, while
// still catching "you configured this and then forgot about it
// entirely."
const _STALE_MULTIPLIER = 3

function lastPolledLabel(row: SourceConfigItem): { text: string; isStale: boolean } {
  if (row.last_polled_at === null) {
    return { text: 'Never polled', isStale: row.is_active }
  }
  const lastPolled = new Date(row.last_polled_at)
  const secondsSince = (Date.now() - lastPolled.getTime()) / 1000
  const isStale = row.is_active && secondsSince > row.poll_interval_seconds * _STALE_MULTIPLIER
  return { text: `Last polled ${lastPolled.toLocaleString()}`, isStale }
}

export function SourceConfig() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['config', 'sources'],
    queryFn: () => apiFetch<SourceConfigItem[]>('/v1/config/sources'),
  })

  const [activeSources, setActiveSources] = useState<Set<Source>>(new Set())
  const [activeDomains, setActiveDomains] = useState<Set<Domain>>(new Set())
  const [fdaFeedUrl, setFdaFeedUrl] = useState('')
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
    setActiveSources(
      new Set(
        query.data
          .filter((row) => row.is_active && TOGGLE_SOURCES.includes(row.source))
          .map((row) => row.source),
      ),
    )
    setActiveDomains(domainsFromRows(query.data))
    setFdaFeedUrl(query.data.find((row) => row.source === 'FDA')?.feed_url ?? '')
  }

  const serverToggleSources = new Set(
    (query.data ?? []).filter((row) => row.is_active && TOGGLE_SOURCES.includes(row.source)).map((row) => row.source),
  )
  const serverFdaFeedUrl = query.data?.find((row) => row.source === 'FDA')?.feed_url ?? ''
  const dirty =
    query.data !== undefined &&
    (!setsEqual(activeSources, serverToggleSources) ||
      !setsEqual(activeDomains, domainsFromRows(query.data)) ||
      fdaFeedUrl !== serverFdaFeedUrl)

  const saveMutation = useMutation({
    mutationFn: () =>
      apiFetch<SourceConfigItem[]>('/v1/config/sources', {
        method: 'POST',
        body: JSON.stringify({
          // FDA has no toggle of its own — it's active exactly when a feed
          // URL is configured, so its inclusion here is derived rather
          // than tracked as its own piece of state.
          sources: [...Array.from(activeSources), ...(fdaFeedUrl.trim() ? (['FDA'] as const) : [])],
          domains: Array.from(activeDomains),
          fda_feed_url: fdaFeedUrl.trim() || null,
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

  const fdaRow = query.data?.find((row) => row.source === 'FDA')
  const [pollResult, setPollResult] = useState<string | null>(null)
  const [fetchResult, setFetchResult] = useState<string | null>(null)

  // Every filing pending for this org (not just ones this call fetched) is
  // then processed in the background by the server — see PollSourceResponse/
  // FetchNowResponse's own docstrings. Invalidating these two queries means
  // whichever page the admin lands on next (Filings, most likely) shows
  // the newly-pending/newly-processing rows immediately rather than on
  // some later background refetch.
  function invalidateFilingsQueries() {
    queryClient.invalidateQueries({ queryKey: ['config', 'sources'] })
    queryClient.invalidateQueries({ queryKey: ['filings-pending'] })
    queryClient.invalidateQueries({ queryKey: ['filings'] })
  }

  function processingSuffix(count: number): string {
    if (count === 0) return ''
    return ` Now processing ${count} filing${count === 1 ? '' : 's'} live — see the Filings page.`
  }

  const pollFdaMutation = useMutation({
    mutationFn: () =>
      apiFetch<{
        source: Source
        new_filing_count: number
        last_polled_at: string | null
        processing_filing_ids: string[]
      }>('/v1/config/sources/FDA/poll', { method: 'POST' }),
    onSuccess: (data) => {
      invalidateFilingsQueries()
      const fetched =
        data.new_filing_count === 0
          ? 'Polled — no new filings found.'
          : `Polled — ${data.new_filing_count} new filing${data.new_filing_count === 1 ? '' : 's'} found.`
      setPollResult(fetched + processingSuffix(data.processing_filing_ids.length))
    },
    onError: (error) => {
      setPollResult(error instanceof ApiError ? error.message : 'Polling FDA failed.')
    },
  })

  const fetchNowMutation = useMutation({
    mutationFn: () =>
      apiFetch<{
        results: { source: Source; new_filing_count: number }[]
        processing_filing_ids: string[]
      }>('/v1/config/sources/fetch-now', {
        method: 'POST',
        body: JSON.stringify({ sources: Array.from(serverToggleSources) }),
      }),
    onSuccess: (data) => {
      invalidateFilingsQueries()
      const perSource = data.results
        .map((result) => `${result.source} ${result.new_filing_count}`)
        .join(', ')
      setFetchResult(`Fetched — ${perSource} new.` + processingSuffix(data.processing_filing_ids.length))
    },
    onError: (error) => {
      setFetchResult(error instanceof ApiError ? error.message : 'Fetching failed.')
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
            <div className="mb-3 flex items-center justify-between">
              <p className="text-sm font-semibold text-slate-900">Regulators</p>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={dirty || serverToggleSources.size === 0}
                loading={fetchNowMutation.isPending}
                onClick={() => {
                  setFetchResult(null)
                  fetchNowMutation.mutate()
                }}
              >
                Fetch now
              </Button>
            </div>
            <div className="flex flex-col gap-2">
              {TOGGLE_SOURCES.map((source) => {
                const row = query.data.find((r) => r.source === source)
                const polled = row ? lastPolledLabel(row) : null
                return (
                  <ToggleRow
                    key={source}
                    label={source}
                    checked={activeSources.has(source)}
                    onChange={() => toggleSource(source)}
                    subtext={
                      polled && (
                        <span className={polled.isStale ? 'text-risk-medium' : 'text-slate-400'}>
                          {polled.isStale ? `⚠ ${polled.text} — check it hasn't been forgotten` : polled.text}
                        </span>
                      )
                    }
                  />
                )
              })}
            </div>
            <p className="mt-1 text-xs text-slate-400">
              {dirty
                ? 'Save your checked regulators before fetching.'
                : serverToggleSources.size === 0
                  ? 'Check at least one regulator above to fetch.'
                  : `Fetches from ${Array.from(serverToggleSources).join(' and ')}.`}
            </p>
            {fetchResult && (
              <p className="mt-2 text-xs text-slate-500">
                {fetchResult}{' '}
                {fetchResult.includes('processing') && (
                  <Link to="/filings" className="text-primary-600 hover:underline">
                    View live progress →
                  </Link>
                )}
              </p>
            )}
            <div className="mt-4 border-t border-slate-200 pt-4">
              <Input
                label="FDA feed URL"
                value={fdaFeedUrl}
                onChange={(e) => {
                  setSaveError(null)
                  setFdaFeedUrl(e.target.value)
                }}
                placeholder="https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml"
                helperText="SEC and FINRA are driven entirely by their own APIs — only FDA needs a feed URL. Any of FDA's public RSS feeds work (Drugs, Recalls/Safety Alerts, Press Releases)."
              />
              <div className="mt-3 flex items-center justify-between gap-3">
                <p className="text-xs text-slate-400">
                  {dirty
                    ? 'Save this URL before polling it.'
                    : !serverFdaFeedUrl
                      ? 'No feed URL configured yet.'
                      : fdaRow
                        ? lastPolledLabel(fdaRow).text
                        : null}
                </p>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={!serverFdaFeedUrl || dirty}
                  loading={pollFdaMutation.isPending}
                  onClick={() => {
                    setPollResult(null)
                    pollFdaMutation.mutate()
                  }}
                >
                  Poll now
                </Button>
              </div>
              {pollResult && (
                <p className="mt-2 text-xs text-slate-500">
                  {pollResult}{' '}
                  {pollResult.includes('processing') && (
                    <Link to="/filings" className="text-primary-600 hover:underline">
                      View live progress →
                    </Link>
                  )}
                </p>
              )}
            </div>
            <p className="mt-3 text-xs text-slate-400">
              SEC and FINRA are polled manually (
              <code className="rounded bg-slate-100 px-1 py-0.5">regradar poll-once</code>) — this project
              has no always-on scheduler, so nothing polls these on its own.
            </p>
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
  subtext,
}: {
  label: string
  checked: boolean
  onChange: () => void
  subtext?: ReactNode
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between rounded-md px-3 py-2 hover:bg-slate-50">
      <span>
        <span className="block text-sm text-slate-700">{label}</span>
        {subtext && <span className="block text-xs">{subtext}</span>}
      </span>
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        className="h-4 w-4 rounded border-slate-300 text-primary-600 focus:ring-primary-600"
      />
    </label>
  )
}
