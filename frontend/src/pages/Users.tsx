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
  last_used_at: string | null
  email: string | null
  sso_provider: string | null
}

interface InviteItem {
  id: string
  code_suffix: string | null
  used_at: string | null
  used_by_email: string | null
  created_at: string
}

interface InviteCreateResponse extends InviteItem {
  code: string
}

const ROLE_OPTIONS: Role[] = ['admin', 'analyst', 'executive', 'legal_counsel', 'eng_lead']

function formatRole(role: Role): string {
  return role
    .split('_')
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ')
}

function RoleCell({ user }: { user: ApiKeyItem }) {
  const queryClient = useQueryClient()
  const [pendingRole, setPendingRole] = useState<Role | null>(null)
  const [rowError, setRowError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: (role: Role) =>
      apiFetch<ApiKeyItem>(`/v1/api-keys/${user.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ role }),
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData<ApiKeyItem[]>(['api-keys'], (prev) =>
        prev?.map((row) => (row.id === updated.id ? updated : row)),
      )
      setPendingRole(null)
      setRowError(null)
    },
    onError: (error) => {
      setRowError(error instanceof ApiError ? error.message : 'Something went wrong.')
    },
  })

  const selected = pendingRole ?? user.role
  const dirty = selected !== user.role

  return (
    <div className="flex items-center gap-2">
      <select
        value={selected}
        onChange={(e) => setPendingRole(e.target.value as Role)}
        className="h-9 rounded-md border border-slate-300 px-2 text-sm text-slate-900 focus:border-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-600"
      >
        {ROLE_OPTIONS.map((role) => (
          <option key={role} value={role}>
            {formatRole(role)}
          </option>
        ))}
      </select>
      {dirty && (
        <button
          type="button"
          disabled={mutation.isPending}
          onClick={() => mutation.mutate(selected)}
          className="rounded-md bg-primary-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-700 disabled:opacity-50"
        >
          {mutation.isPending ? 'Saving…' : 'Save'}
        </button>
      )}
      {rowError && <span className="text-xs text-risk-critical">{rowError}</span>}
    </div>
  )
}

function CreateInviteModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [createdCode, setCreatedCode] = useState<string | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)

  const mutation = useMutation({
    mutationFn: () => apiFetch<InviteCreateResponse>('/v1/invites', { method: 'POST' }),
    onSuccess: (data) => {
      setCreatedCode(data.code)
      queryClient.invalidateQueries({ queryKey: ['invites'] })
    },
  })

  function handleClose() {
    setCreatedCode(null)
    setAcknowledged(false)
    mutation.reset()
    onClose()
  }

  const showingCode = createdCode !== null

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title={showingCode ? 'Invite code created' : 'Create an invite'}
      dismissable={!showingCode}
    >
      {showingCode ? (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-600">
            This invite code is shown <strong>only once</strong>. Share it with the person you&rsquo;re
            inviting — they&rsquo;ll need it to create their account, and it can only be used once.
          </p>
          <div className="rounded-md border border-slate-300 bg-slate-50 p-3">
            <code className="break-all font-mono text-xs text-slate-900">{createdCode}</code>
          </div>
          <label className="flex items-start gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
            />
            I&rsquo;ve shared or saved this code somewhere safe.
          </label>
          <Button type="button" disabled={!acknowledged} onClick={handleClose}>
            Done
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-600">
            Generates a single-use signup code. The person who uses it picks their own role at
            signup (never Admin) — this code only controls whether they can create an account at
            all.
          </p>
          {mutation.isError && (
            <p className="text-sm text-risk-critical">
              {mutation.error instanceof ApiError
                ? mutation.error.message
                : 'Something went wrong creating this invite.'}
            </p>
          )}
          <div className="flex justify-end gap-3">
            <Button type="button" variant="secondary" onClick={handleClose}>
              Cancel
            </Button>
            <Button type="button" loading={mutation.isPending} onClick={() => mutation.mutate()}>
              Generate code
            </Button>
          </div>
        </div>
      )}
    </Modal>
  )
}

function InvitesList() {
  const query = useQuery({
    queryKey: ['invites'],
    queryFn: () => apiFetch<InviteItem[]>('/v1/invites'),
  })

  if (query.isPending) return <p className="text-sm text-slate-500">Loading invites…</p>
  if (query.isError) return null // Non-critical panel — the user list above already shows any real error state.
  if (query.data.length === 0) {
    return <p className="text-sm text-slate-500">No invites created yet.</p>
  }

  return (
    <div className="flex flex-col gap-2">
      {query.data.map((invite) => (
        <div key={invite.id} className="flex items-center justify-between text-sm">
          <span className="font-mono text-slate-600">
            rrinv_&hellip;{invite.code_suffix ?? '????'}
          </span>
          {invite.used_at ? (
            <span className="text-slate-400">
              Used by {invite.used_by_email ?? 'someone'} on{' '}
              {new Date(invite.used_at).toLocaleDateString()}
            </span>
          ) : (
            <span className="text-risk-low-text">Unused</span>
          )}
        </div>
      ))}
    </div>
  )
}

export function Users() {
  const [inviteModalOpen, setInviteModalOpen] = useState(false)
  const query = useQuery({
    queryKey: ['api-keys'],
    queryFn: () => apiFetch<ApiKeyItem[]>('/v1/api-keys'),
  })

  const forbidden = query.isError && query.error instanceof ApiError && query.error.status === 403

  // Only rows that can actually sign in (email/password or Google SSO) —
  // a bare programmatic API key has a role too, but changing it belongs
  // on the API Keys screen (create/revoke), not here.
  const users = query.data?.filter((row) => row.email || row.sso_provider) ?? []

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Manage Users</h1>
        {!forbidden && <Button onClick={() => setInviteModalOpen(true)}>Invite a teammate</Button>}
      </div>

      {!forbidden && (
        <Card>
          <p className="mb-3 text-sm font-semibold text-slate-900">Invite codes</p>
          <InvitesList />
        </Card>
      )}

      {query.isPending && (
        <Card>
          <p className="text-sm text-slate-500">Loading…</p>
        </Card>
      )}

      {forbidden && (
        <Card>
          <p className="text-sm text-slate-500">You don&rsquo;t have permission to manage users.</p>
        </Card>
      )}

      {query.isError && !forbidden && (
        <Card>
          <p className="text-sm text-risk-critical">
            {query.error instanceof ApiError ? query.error.message : 'Something went wrong.'}
          </p>
        </Card>
      )}

      {query.isSuccess && users.length === 0 && (
        <Card>
          <p className="text-sm text-slate-500">No users have signed in yet.</p>
        </Card>
      )}

      {query.isSuccess && users.length > 0 && (
        <div className="flex flex-col gap-3">
          {users.map((user) => (
            <Card key={user.id}>
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="min-w-0">
                  <p className="font-medium text-slate-900">{user.owner_label}</p>
                  <p className="mt-0.5 text-sm text-slate-500">
                    {user.email ?? 'Google account'}
                    {user.sso_provider && ' · Google'}
                  </p>
                  <p className="mt-0.5 text-xs text-slate-400">
                    {user.last_used_at
                      ? `Last active ${new Date(user.last_used_at).toLocaleString()}`
                      : 'Never signed in'}
                  </p>
                </div>
                <RoleCell user={user} />
              </div>
            </Card>
          ))}
        </div>
      )}

      <CreateInviteModal isOpen={inviteModalOpen} onClose={() => setInviteModalOpen(false)} />
    </div>
  )
}
