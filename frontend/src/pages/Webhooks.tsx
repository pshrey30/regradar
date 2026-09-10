import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { Badge, type DomainValue, type RiskLevel } from '../components/Badge'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Modal } from '../components/Modal'
import { ApiError, apiFetch } from '../lib/api'

interface WebhookItem {
  id: string
  url: string
  is_active: boolean
  filter_domain: DomainValue | null
  filter_min_risk: RiskLevel | null
  created_at: string
  last_delivery_status: 'pending' | 'sent' | 'failed' | 'retrying' | null
  last_delivery_at: string | null
  recent_failure_count: number
}

interface WebhookCreateResponse extends WebhookItem {
  hmac_secret: string
}

const DOMAIN_OPTIONS: DomainValue[] = ['financial', 'clinical', 'environmental', 'other']
const RISK_OPTIONS: RiskLevel[] = ['low', 'medium', 'high', 'critical']

function DeliveryHealth({ webhook }: { webhook: WebhookItem }) {
  if (webhook.last_delivery_status === null) {
    return <span className="text-xs text-slate-400">No deliveries yet</span>
  }
  if (webhook.last_delivery_status === 'failed' || webhook.recent_failure_count > 0) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-risk-critical">
        <span className="h-2 w-2 rounded-full bg-risk-critical" />
        {webhook.recent_failure_count} recent failure{webhook.recent_failure_count === 1 ? '' : 's'}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-risk-low">
      <span className="h-2 w-2 rounded-full bg-risk-low" />
      Healthy
    </span>
  )
}

function RegisterWebhookModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [url, setUrl] = useState('')
  const [filterDomain, setFilterDomain] = useState('')
  const [filterMinRisk, setFilterMinRisk] = useState('')
  const [createdSecret, setCreatedSecret] = useState<string | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<WebhookCreateResponse>('/v1/webhooks', {
        method: 'POST',
        body: JSON.stringify({
          url,
          filter_domain: filterDomain || null,
          filter_min_risk: filterMinRisk || null,
        }),
      }),
    onSuccess: (data) => {
      setCreatedSecret(data.hmac_secret)
      queryClient.invalidateQueries({ queryKey: ['webhooks'] })
    },
  })

  function reset() {
    setUrl('')
    setFilterDomain('')
    setFilterMinRisk('')
    setCreatedSecret(null)
    setAcknowledged(false)
    mutation.reset()
  }

  function handleClose() {
    reset()
    onClose()
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    mutation.mutate()
  }

  // The secret step is deliberately non-dismissable until the user
  // checks the acknowledgment box — it's shown exactly once, ever.
  const showingSecret = createdSecret !== null

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title={showingSecret ? 'Webhook registered' : 'Register webhook'}
      dismissable={!showingSecret}
    >
      {showingSecret ? (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-600">
            This signing secret is shown <strong>only once</strong>. Store it now — RegRadar
            cannot show it to you again.
          </p>
          <div className="rounded-md border border-slate-300 bg-slate-50 p-3">
            <code className="break-all font-mono text-xs text-slate-900">{createdSecret}</code>
          </div>
          <label className="flex items-start gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
            />
            I&rsquo;ve saved this secret somewhere safe.
          </label>
          <Button type="button" disabled={!acknowledged} onClick={handleClose}>
            Done
          </Button>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="webhook-url" className="text-sm font-medium text-slate-900">
              URL
            </label>
            <input
              id="webhook-url"
              type="url"
              required
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/regradar-webhook"
              className="h-10 rounded-md border border-slate-300 px-3 text-sm text-slate-900 focus:border-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-600"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="webhook-domain" className="text-sm font-medium text-slate-900">
              Domain filter (optional)
            </label>
            <select
              id="webhook-domain"
              value={filterDomain}
              onChange={(e) => setFilterDomain(e.target.value)}
              className="h-10 rounded-md border border-slate-300 px-3 text-sm capitalize text-slate-900 focus:border-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-600"
            >
              <option value="">All domains</option>
              {DOMAIN_OPTIONS.map((domain) => (
                <option key={domain} value={domain}>
                  {domain}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="webhook-risk" className="text-sm font-medium text-slate-900">
              Minimum risk (optional)
            </label>
            <select
              id="webhook-risk"
              value={filterMinRisk}
              onChange={(e) => setFilterMinRisk(e.target.value)}
              className="h-10 rounded-md border border-slate-300 px-3 text-sm capitalize text-slate-900 focus:border-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-600"
            >
              <option value="">Any risk level</option>
              {RISK_OPTIONS.map((risk) => (
                <option key={risk} value={risk}>
                  {risk}
                </option>
              ))}
            </select>
          </div>
          {mutation.isError && (
            <p className="text-sm text-risk-critical">
              {mutation.error instanceof ApiError
                ? mutation.error.message
                : 'Something went wrong registering this webhook.'}
            </p>
          )}
          <div className="flex justify-end gap-3">
            <Button type="button" variant="secondary" onClick={handleClose}>
              Cancel
            </Button>
            <Button type="submit" loading={mutation.isPending}>
              Register
            </Button>
          </div>
        </form>
      )}
    </Modal>
  )
}

function DeleteWebhookModal({
  webhook,
  onClose,
}: {
  webhook: WebhookItem | null
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (id: string) => apiFetch(`/v1/webhooks/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] })
      onClose()
    },
  })

  return (
    <Modal isOpen={webhook !== null} onClose={onClose} title="Delete webhook">
      {webhook && (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-600">
            Delete the webhook for <span className="font-mono text-slate-900">{webhook.url}</span>
            ? RegRadar will stop sending it any deliveries immediately. This can&rsquo;t be undone.
          </p>
          {mutation.isError && (
            <p className="text-sm text-risk-critical">
              {mutation.error instanceof ApiError
                ? mutation.error.message
                : 'Something went wrong deleting this webhook.'}
            </p>
          )}
          <div className="flex justify-end gap-3">
            <Button type="button" variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              loading={mutation.isPending}
              onClick={() => mutation.mutate(webhook.id)}
            >
              Delete webhook
            </Button>
          </div>
        </div>
      )}
    </Modal>
  )
}

export function Webhooks() {
  const [registerOpen, setRegisterOpen] = useState(false)
  const [webhookToDelete, setWebhookToDelete] = useState<WebhookItem | null>(null)

  const query = useQuery({
    queryKey: ['webhooks'],
    queryFn: () => apiFetch<WebhookItem[]>('/v1/webhooks'),
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Webhooks</h1>
        <Button onClick={() => setRegisterOpen(true)}>Register webhook</Button>
      </div>

      {query.isError && (
        <Card>
          <p className="text-sm text-risk-critical">
            {query.error instanceof ApiError
              ? query.error.message
              : 'Something went wrong loading webhooks.'}
          </p>
        </Card>
      )}

      {query.isSuccess && query.data.length === 0 && (
        <Card>
          <p className="text-sm text-slate-500">
            No webhooks registered yet. Register one to receive filing deliveries at a URL you
            control.
          </p>
        </Card>
      )}

      {query.isSuccess && query.data.length > 0 && (
        <div className="flex flex-col gap-3">
          {query.data.map((webhook) => (
            <Card key={webhook.id}>
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-mono text-sm text-slate-900">{webhook.url}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <span
                      className={[
                        'rounded-full border px-2.5 py-0.5 text-xs font-medium',
                        webhook.is_active
                          ? 'border-risk-low bg-white text-risk-low-text'
                          : 'border-slate-200 bg-slate-100 text-slate-500',
                      ].join(' ')}
                    >
                      {webhook.is_active ? 'Active' : 'Inactive'}
                    </span>
                    {webhook.filter_domain && (
                      <Badge variant="domain" value={webhook.filter_domain} />
                    )}
                    {webhook.filter_min_risk && (
                      <Badge variant="risk" value={webhook.filter_min_risk} />
                    )}
                  </div>
                  <div className="mt-2">
                    <DeliveryHealth webhook={webhook} />
                  </div>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setWebhookToDelete(webhook)}
                >
                  Delete
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      <RegisterWebhookModal isOpen={registerOpen} onClose={() => setRegisterOpen(false)} />
      <DeleteWebhookModal webhook={webhookToDelete} onClose={() => setWebhookToDelete(null)} />
    </div>
  )
}
