import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Input } from '../components/Input'
import { ApiError, apiFetch } from '../lib/api'

interface SearchSource {
  filing_id: string
  excerpt: string
  entity_name: string
}

interface SearchResponse {
  answer: string | null
  sources: SearchSource[]
  degraded: boolean
}

export function Search() {
  const [query, setQuery] = useState('')
  const [submittedQuery, setSubmittedQuery] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: (searchQuery: string) =>
      apiFetch<SearchResponse>('/v1/filings/search', {
        method: 'POST',
        body: JSON.stringify({ query: searchQuery }),
      }),
  })

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!query.trim()) return
    setSubmittedQuery(query)
    mutation.mutate(query)
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold text-slate-900">Ask RegRadar</h1>

      <form onSubmit={handleSubmit} className="flex flex-col gap-3 sm:flex-row">
        <div className="flex-1">
          <Input
            size="lg"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="What has the SEC said about late 10-K filings?"
            aria-label="Ask a question about past filings"
          />
        </div>
        <Button
          type="submit"
          size="lg"
          className="w-full sm:w-auto"
          loading={mutation.isPending}
          disabled={!query.trim()}
        >
          Ask
        </Button>
      </form>

      {mutation.isError && (
        <Card>
          <p className="text-sm text-risk-critical">
            {mutation.error instanceof ApiError
              ? mutation.error.message
              : 'Something went wrong running this search.'}
          </p>
        </Card>
      )}

      {mutation.isSuccess && (
        <div className="flex flex-col gap-4">
          {mutation.data.degraded && (
            <div className="flex items-start justify-between gap-4 rounded-lg border-2 border-risk-medium bg-white p-4 text-sm">
              <div className="flex items-start gap-2.5">
                <svg
                  viewBox="0 0 20 20"
                  fill="currentColor"
                  aria-hidden="true"
                  className="mt-0.5 h-5 w-5 shrink-0 text-risk-medium"
                >
                  <path
                    fillRule="evenodd"
                    d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l6.28 11.178c.75 1.334-.213 2.986-1.742 2.986H3.72c-1.53 0-2.493-1.652-1.743-2.986L8.257 3.1ZM10 7a.75.75 0 0 1 .75.75v3a.75.75 0 0 1-1.5 0v-3A.75.75 0 0 1 10 7Zm0 8a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z"
                    clipRule="evenodd"
                  />
                </svg>
                <div>
                  <p className="font-semibold text-slate-900">Answering is temporarily limited</p>
                  <p className="mt-0.5 text-slate-600">
                    Natural-language answering is unavailable right now — the raw search results
                    below are still accurate.
                  </p>
                </div>
              </div>
              {submittedQuery && (
                <Button
                  variant="secondary"
                  size="sm"
                  loading={mutation.isPending}
                  onClick={() => mutation.mutate(submittedQuery)}
                  className="shrink-0"
                >
                  Retry answer
                </Button>
              )}
            </div>
          )}

          {mutation.data.answer && (
            <Card>
              <h2 className="mb-2 text-sm font-semibold text-slate-900">Answer</h2>
              <p className="whitespace-pre-line text-sm text-slate-700">{mutation.data.answer}</p>
            </Card>
          )}

          <div className="flex flex-col gap-2">
            <h2 className="text-sm font-semibold text-slate-900">
              {mutation.data.sources.length > 0
                ? `Sources (${mutation.data.sources.length})`
                : 'Sources'}
            </h2>
            {mutation.data.sources.length === 0 ? (
              <Card>
                <p className="text-sm text-slate-500">
                  No relevant filings were found for &ldquo;{submittedQuery}&rdquo;.
                </p>
              </Card>
            ) : (
              mutation.data.sources.map((source, index) => (
                <Link
                  key={`${source.filing_id}-${index}`}
                  to={`/filings/${source.filing_id}`}
                  className="rounded-lg border border-slate-200 bg-white p-4 text-sm hover:bg-slate-50"
                >
                  <p className="font-medium text-slate-900">{source.entity_name}</p>
                  <p className="mt-1 text-slate-600">{source.excerpt}</p>
                </Link>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}
