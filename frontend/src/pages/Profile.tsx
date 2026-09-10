import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'

import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Input } from '../components/Input'
import { ApiError, apiFetch } from '../lib/api'

interface MeResponse {
  role: string
  organization_id: string | null
  display_name: string
  email: string | null
  has_password: boolean
}

function formatRole(role: string): string {
  return role
    .split('_')
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ')
}

function NameForm({ me }: { me: MeResponse }) {
  const queryClient = useQueryClient()
  const [displayName, setDisplayName] = useState(me.display_name)
  const [savedJustNow, setSavedJustNow] = useState(false)

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<MeResponse>('/v1/me', {
        method: 'PATCH',
        body: JSON.stringify({ display_name: displayName }),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(['me', 'profile'], data)
      setSavedJustNow(true)
      setTimeout(() => setSavedJustNow(false), 3000)
    },
  })

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (displayName.trim()) mutation.mutate()
  }

  return (
    <Card>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">Name</h2>
        {savedJustNow && <span className="text-sm text-risk-low-text">Saved</span>}
      </div>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="flex-1">
          <Input
            label="Display name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            required
          />
        </div>
        {mutation.isError && (
          <p className="text-sm text-risk-critical">
            {mutation.error instanceof ApiError ? mutation.error.message : 'Something went wrong.'}
          </p>
        )}
        <Button
          type="submit"
          disabled={!displayName.trim() || displayName === me.display_name}
          loading={mutation.isPending}
        >
          Save
        </Button>
      </form>
    </Card>
  )
}

function PasswordForm() {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [savedJustNow, setSavedJustNow] = useState(false)

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch('/v1/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      }),
    onSuccess: () => {
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setFormError(null)
      setSavedJustNow(true)
      setTimeout(() => setSavedJustNow(false), 3000)
    },
    onError: (error) => {
      setFormError(error instanceof ApiError ? error.message : 'Something went wrong.')
    },
  })

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    if (newPassword !== confirmPassword) {
      setFormError("New passwords don't match.")
      return
    }
    mutation.mutate()
  }

  return (
    <Card>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">Password</h2>
        {savedJustNow && <span className="text-sm text-risk-low-text">Password updated</span>}
      </div>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <Input
          label="Current password"
          type="password"
          required
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
        />
        <Input
          label="New password"
          type="password"
          required
          minLength={8}
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          placeholder="At least 8 characters"
        />
        <Input
          label="Confirm new password"
          type="password"
          required
          minLength={8}
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
        />
        {formError && <p className="text-sm text-risk-critical">{formError}</p>}
        <div>
          <Button
            type="submit"
            disabled={!currentPassword || !newPassword || !confirmPassword}
            loading={mutation.isPending}
          >
            Update password
          </Button>
        </div>
      </form>
    </Card>
  )
}

export function Profile() {
  const query = useQuery({
    queryKey: ['me', 'profile'],
    queryFn: () => apiFetch<MeResponse>('/v1/me'),
  })

  return (
    <div className="flex max-w-lg flex-col gap-4">
      <h1 className="text-xl font-semibold text-slate-900">Profile</h1>

      {query.isPending && (
        <Card>
          <p className="text-sm text-slate-500">Loading…</p>
        </Card>
      )}

      {query.isError && (
        <Card>
          <p className="text-sm text-risk-critical">
            {query.error instanceof ApiError ? query.error.message : 'Something went wrong.'}
          </p>
        </Card>
      )}

      {query.isSuccess && (
        <>
          <Card>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="text-slate-500">Role</dt>
              <dd className="font-medium text-slate-900">{formatRole(query.data.role)}</dd>
              {query.data.email && (
                <>
                  <dt className="text-slate-500">Email</dt>
                  <dd className="font-medium text-slate-900">{query.data.email}</dd>
                </>
              )}
            </dl>
            <p className="mt-3 text-xs text-slate-400">
              Your role is managed by an Admin — see Manage Users if you need it changed.
            </p>
          </Card>

          <NameForm me={query.data} />

          {query.data.has_password ? (
            <PasswordForm />
          ) : (
            <Card>
              <h2 className="mb-1 text-sm font-semibold text-slate-900">Password</h2>
              <p className="text-sm text-slate-500">
                You sign in with Google — there&rsquo;s no password to change here.
              </p>
            </Card>
          )}
        </>
      )}
    </div>
  )
}
