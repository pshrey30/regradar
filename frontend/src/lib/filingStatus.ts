import { API_BASE_URL } from './api'

export const FILING_STATUS_LABELS: Record<string, string> = {
  ingested: 'Ingested',
  classifying: 'Classifying',
  needs_classification: 'Needs classification',
  needs_review: 'Needs review',
  needs_organization_setup: 'Needs organization setup',
  retrieving: 'Retrieving context',
  analyzing: 'Analyzing',
  summarizing: 'Summarizing',
  delivering: 'Delivering',
  complete: 'Complete',
  failed: 'Failed',
}

// FE-04's ticket calls for Supabase Realtime, but this deployment's
// Postgres is local Docker, not a real Supabase project (see migration
// 0016's docstring) — this derives our own backend's WebSocket URL from
// the same API_BASE_URL every other request already uses.
export function filingStatusWebSocketUrl(filingId: string): string {
  const wsBase = API_BASE_URL.replace(/^http/, 'ws')
  return `${wsBase}/v1/filings/${filingId}/status/ws`
}
