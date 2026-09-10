import { useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'

import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Input } from '../components/Input'
import { API_BASE_URL, ApiError, apiFetch } from '../lib/api'

const _ERROR_MESSAGES: Record<string, string> = {
  state_mismatch: 'Your login attempt expired or was invalid. Please try again.',
  sso_failed: 'Google sign-in failed. Please try again.',
}

type Mode = 'login' | 'signup'

export function Login() {
  const [searchParams] = useSearchParams()
  const oauthErrorCode = searchParams.get('error')

  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [displayName, setDisplayName] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    if (mode === 'signup' && password !== confirmPassword) {
      setFormError("Passwords don't match.")
      return
    }
    setSubmitting(true)
    try {
      const path = mode === 'login' ? '/v1/auth/login' : '/v1/auth/signup'
      const body =
        mode === 'login'
          ? { email, password }
          : { email, password, display_name: displayName || undefined }
      await apiFetch(path, { method: 'POST', body: JSON.stringify(body), skipAuthRedirect: true })
      // The session cookie is now set — a full reload (not client-side
      // navigation) is the simplest way to make AuthProvider's GET /v1/me
      // re-run and pick it up, matching how the Google flow's own
      // redirect-back-to-frontend already forces a fresh load.
      window.location.href = '/filings'
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
      <Card className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <h1 className="mb-1 text-xl font-semibold text-slate-900">RegRadar</h1>
          <p className="text-sm text-slate-500">Regulatory filing intelligence</p>
        </div>

        {oauthErrorCode && (
          <p className="mb-4 rounded-md border border-risk-critical bg-white px-3 py-2 text-sm text-risk-critical">
            {_ERROR_MESSAGES[oauthErrorCode] ?? 'Something went wrong signing in.'}
          </p>
        )}

        <a href={`${API_BASE_URL}/v1/auth/google/login`}>
          <Button variant="secondary" size="lg" className="w-full">
            Sign in with Google
          </Button>
        </a>

        <div className="my-4 flex items-center gap-3">
          <div className="h-px flex-1 bg-slate-200" />
          <span className="text-xs text-slate-400">or</span>
          <div className="h-px flex-1 bg-slate-200" />
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          {mode === 'signup' && (
            <Input
              label="Name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Your name"
            />
          )}
          <Input
            label="Email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
          />
          <div className="relative">
            <Input
              label="Password"
              type={showPassword ? 'text' : 'password'}
              required
              minLength={mode === 'signup' ? 8 : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === 'signup' ? 'At least 8 characters' : undefined}
              className="pr-14"
            />
            <button
              type="button"
              onClick={() => setShowPassword((show) => !show)}
              className="absolute bottom-0 right-0 flex h-10 items-center pr-3 text-xs font-medium text-primary-600 hover:underline"
            >
              {showPassword ? 'Hide' : 'Show'}
            </button>
          </div>
          {mode === 'signup' && (
            <Input
              label="Confirm password"
              type={showPassword ? 'text' : 'password'}
              required
              minLength={8}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Re-enter your password"
            />
          )}
          {formError && <p className="text-sm text-risk-critical">{formError}</p>}
          <Button type="submit" variant="primary" size="lg" className="w-full" loading={submitting}>
            {mode === 'login' ? 'Sign in' : 'Create account'}
          </Button>
        </form>

        <p className="mt-4 text-center text-sm text-slate-500">
          {mode === 'login' ? "Don't have an account? " : 'Already have an account? '}
          <button
            type="button"
            className="font-medium text-primary-600 hover:underline"
            onClick={() => {
              setMode(mode === 'login' ? 'signup' : 'login')
              setFormError(null)
              setConfirmPassword('')
            }}
          >
            {mode === 'login' ? 'Sign up' : 'Sign in'}
          </button>
        </p>
      </Card>
    </div>
  )
}
