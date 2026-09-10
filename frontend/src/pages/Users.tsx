import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { Card } from '../components/Card'
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

export function Users() {
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
      <h1 className="text-xl font-semibold text-slate-900">Manage Users</h1>

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
    </div>
  )
}
