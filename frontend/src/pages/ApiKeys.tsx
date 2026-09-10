import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Modal } from '../components/Modal'
import { ApiError, apiFetch } from '../lib/api'

type Role = 'admin' | 'analyst' | 'executive' | 'legal_counsel' | 'eng_lead'

interface ApiKeyItem {
  id: string
  owner_label: string
  role: Role
  is_active: boolean
  rate_limit_per_minute: number
  key_suffix: string | null
  created_at: string
  last_used_at: string | null
}

interface ApiKeyCreateResponse extends ApiKeyItem {
  key: string
}

const ROLE_OPTIONS: Role[] = ['admin', 'analyst', 'executive', 'legal_counsel', 'eng_lead']

function formatRole(role: Role): string {
  return role
    .split('_')
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ')
}

function CreateApiKeyModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [ownerLabel, setOwnerLabel] = useState('')
  const [role, setRole] = useState<Role>('analyst')
  const [createdKey, setCreatedKey] = useState<string | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<ApiKeyCreateResponse>('/v1/api-keys', {
        method: 'POST',
        body: JSON.stringify({ owner_label: ownerLabel, role }),
      }),
    onSuccess: (data) => {
      setCreatedKey(data.key)
      queryClient.invalidateQueries({ queryKey: ['api-keys'] })
    },
  })

  function reset() {
    setOwnerLabel('')
    setRole('analyst')
    setCreatedKey(null)
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

  const showingKey = createdKey !== null

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title={showingKey ? 'API key created' : 'Create API key'}
      dismissable={!showingKey}
    >
      {showingKey ? (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-600">
            This key is shown <strong>only once</strong>. Store it now — RegRadar cannot show it
            to you again.
          </p>
          <div className="rounded-md border border-slate-300 bg-slate-50 p-3">
            <code className="break-all font-mono text-xs text-slate-900">{createdKey}</code>
          </div>
          <label className="flex items-start gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
            />
            I&rsquo;ve saved this key somewhere safe.
          </label>
          <Button type="button" disabled={!acknowledged} onClick={handleClose}>
            Done
          </Button>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="key-owner-label" className="text-sm font-medium text-slate-900">
              Label
            </label>
            <input
              id="key-owner-label"
              type="text"
              required
              value={ownerLabel}
              onChange={(e) => setOwnerLabel(e.target.value)}
              placeholder="e.g. CI pipeline, analyst laptop"
              className="h-10 rounded-md border border-slate-300 px-3 text-sm text-slate-900 focus:border-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-600"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="key-role" className="text-sm font-medium text-slate-900">
              Role
            </label>
            <select
              id="key-role"
              value={role}
              onChange={(e) => setRole(e.target.value as Role)}
              className="h-10 rounded-md border border-slate-300 px-3 text-sm text-slate-900 focus:border-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-600"
            >
              {ROLE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {formatRole(option)}
                </option>
              ))}
            </select>
          </div>
          {mutation.isError && (
            <p className="text-sm text-risk-critical">
              {mutation.error instanceof ApiError
                ? mutation.error.message
                : 'Something went wrong creating this API key.'}
            </p>
          )}
          <div className="flex justify-end gap-3">
            <Button type="button" variant="secondary" onClick={handleClose}>
              Cancel
            </Button>
            <Button type="submit" loading={mutation.isPending}>
              Create
            </Button>
          </div>
        </form>
      )}
    </Modal>
  )
}

function RevokeApiKeyModal({ apiKey, onClose }: { apiKey: ApiKeyItem | null; onClose: () => void }) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (id: string) => apiFetch(`/v1/api-keys/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] })
      onClose()
    },
  })

  return (
    <Modal isOpen={apiKey !== null} onClose={onClose} title="Revoke API key">
      {apiKey && (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-600">
            Revoke <span className="font-medium text-slate-900">{apiKey.owner_label}</span>
            {apiKey.key_suffix && (
              <span className="font-mono text-slate-500"> (••••{apiKey.key_suffix})</span>
            )}
            ? Any request using this key will start failing immediately. This can&rsquo;t be
            undone.
          </p>
          {mutation.isError && (
            <p className="text-sm text-risk-critical">
              {mutation.error instanceof ApiError
                ? mutation.error.message
                : 'Something went wrong revoking this API key.'}
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
              onClick={() => mutation.mutate(apiKey.id)}
            >
              Revoke key
            </Button>
          </div>
        </div>
      )}
    </Modal>
  )
}

export function ApiKeys() {
  const [createOpen, setCreateOpen] = useState(false)
  const [keyToRevoke, setKeyToRevoke] = useState<ApiKeyItem | null>(null)

  const query = useQuery({
    queryKey: ['api-keys'],
    queryFn: () => apiFetch<ApiKeyItem[]>('/v1/api-keys'),
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">API Keys</h1>
        <Button onClick={() => setCreateOpen(true)}>Create key</Button>
      </div>

      {query.isError && (
        <Card>
          <p className="text-sm text-risk-critical">
            {query.error instanceof ApiError
              ? query.error.message
              : 'Something went wrong loading API keys.'}
          </p>
        </Card>
      )}

      {query.isSuccess && query.data.length === 0 && (
        <Card>
          <p className="text-sm text-slate-500">
            No API keys created yet. Create one to authenticate a script or integration.
          </p>
        </Card>
      )}

      {query.isSuccess && query.data.length > 0 && (
        <div className="flex flex-col gap-3">
          {query.data.map((apiKey) => (
            <Card key={apiKey.id}>
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium text-slate-900">{apiKey.owner_label}</p>
                    <span
                      className={[
                        'rounded-full border px-2.5 py-0.5 text-xs font-medium',
                        apiKey.is_active
                          ? 'border-risk-low bg-white text-risk-low-text'
                          : 'border-slate-200 bg-slate-100 text-slate-500',
                      ].join(' ')}
                    >
                      {apiKey.is_active ? 'Active' : 'Revoked'}
                    </span>
                  </div>
                  <p className="mt-1 font-mono text-sm text-slate-500">
                    {apiKey.key_suffix ? `rr_••••${apiKey.key_suffix}` : 'rr_••••'}
                  </p>
                  <p className="mt-2 text-xs text-slate-500">
                    {formatRole(apiKey.role)} · Last used{' '}
                    {apiKey.last_used_at
                      ? new Date(apiKey.last_used_at).toLocaleString()
                      : 'never'}
                  </p>
                </div>
                {apiKey.is_active && (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setKeyToRevoke(apiKey)}
                  >
                    Revoke
                  </Button>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}

      <CreateApiKeyModal isOpen={createOpen} onClose={() => setCreateOpen(false)} />
      <RevokeApiKeyModal apiKey={keyToRevoke} onClose={() => setKeyToRevoke(null)} />
    </div>
  )
}
