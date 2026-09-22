import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Input } from '../components/Input'
import { ApiError, apiFetch } from '../lib/api'

// Google sign-in is temporarily removed from the UI (new Google signups
// are disabled server-side too — see auth.py's google login/callback
// docstrings) while invite-gated signup is the only account-creation
// path. Existing Google-linked accounts are untouched by this — the
// backend still resolves an existing identity on login, this UI simply
// no longer offers a way to start that flow. state_mismatch/sso_failed
// are kept here since a stale bookmark to the old Google callback URL
// could still redirect back with one of these.
const _ERROR_MESSAGES: Record<string, string> = {
  state_mismatch: 'Your login attempt expired or was invalid. Please try again.',
  sso_failed: 'Google sign-in failed. Please try again.',
  invalid_invite: 'That invite code is invalid or has already been used.',
  invalid_role: 'Not a valid role to sign up as.',
  google_disabled: 'Google sign-in is temporarily unavailable — please use email and password.',
}

type Mode = 'login' | 'signup' | 'signup-org'

// Every role a signing-up person may choose for themselves — Admin is
// deliberately absent. An Admin account is only ever granted by another
// Admin (Manage Users), never self-selected at signup — see
// schemas/auth.py's SELF_SELECTABLE_ROLES, which this mirrors exactly.
const SELF_SELECTABLE_ROLES = [
  { value: 'analyst', label: 'Analyst' },
  { value: 'legal_counsel', label: 'Legal Counsel' },
  { value: 'eng_lead', label: 'Engineering Lead' },
  { value: 'executive', label: 'Executive' },
] as const

export function Login() {
  const [searchParams] = useSearchParams()
  const oauthErrorCode = searchParams.get('error')

  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [displayName, setDisplayName] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [orgName, setOrgName] = useState('')
  const [role, setRole] = useState<(typeof SELF_SELECTABLE_ROLES)[number]['value']>('analyst')
  const [formError, setFormError] = useState<string | null>(null)
  const [signupSuccess, setSignupSuccess] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    setSignupSuccess(false)
    if ((mode === 'signup' || mode === 'signup-org') && password !== confirmPassword) {
      setFormError("Passwords don't match.")
      return
    }
    setSubmitting(true)
    try {
      if (mode === 'login') {
        await apiFetch('/v1/auth/login', {
          method: 'POST',
          body: JSON.stringify({ email, password }),
          skipAuthRedirect: true,
        })
        // The session cookie is now set — a full reload (not client-side
        // navigation) is the simplest way to make AuthProvider's GET
        // /v1/me re-run and pick it up, matching how the Google flow's
        // own redirect-back-to-frontend already forces a fresh load.
        window.location.href = '/filings'
        return
      }

      if (mode === 'signup-org') {
        await apiFetch('/v1/auth/signup-org', {
          method: 'POST',
          body: JSON.stringify({
            org_name: orgName,
            display_name: displayName,
            email,
            password,
          }),
          skipAuthRedirect: true,
        })
        setMode('login')
        setSignupSuccess(true)
        setPassword('')
        setConfirmPassword('')
        setDisplayName('')
        setOrgName('')
        return
      }

      // Signup deliberately doesn't log the new account in — creating an
      // account and signing in are two separate, explicit actions from
      // the user's point of view, so land them back on the sign-in form
      // instead of silently dropping them into the dashboard.
      await apiFetch('/v1/auth/signup', {
        method: 'POST',
        body: JSON.stringify({
          email,
          password,
          display_name: displayName || undefined,
          invite_code: inviteCode,
          role,
        }),
        skipAuthRedirect: true,
      })
      setMode('login')
      setSignupSuccess(true)
      setPassword('')
      setConfirmPassword('')
      setDisplayName('')
      setInviteCode('')
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-white p-4">
      {/* Same radial primary-tint treatment as the Landing hero — carries
          the visual thread across the "get started" -> login handoff
          instead of dropping into a flat, unrelated screen. */}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_0%,_rgba(79,70,229,0.08),_transparent_60%)]" />

      <div className="relative w-full max-w-sm animate-fade-in-up">
        <Link
          to="/"
          className="mb-6 flex items-center justify-center gap-2 font-mono text-sm font-semibold tracking-[0.2em] text-slate-900"
        >
          REGRADAR
        </Link>

        <Card className="w-full">
          <div className="mb-6 text-center">
            <h1 className="mb-1 text-xl font-semibold text-slate-900">
              {mode === 'login' ? 'Welcome back' : mode === 'signup-org' ? 'Create your organization' : 'Create your account'}
            </h1>
            <p className="text-sm text-slate-500">Regulatory filing intelligence</p>
          </div>

        {oauthErrorCode && (
          <p className="mb-4 rounded-md border border-risk-critical bg-white px-3 py-2 text-sm text-risk-critical">
            {_ERROR_MESSAGES[oauthErrorCode] ?? 'Something went wrong signing in.'}
          </p>
        )}

        {signupSuccess && (
          <p className="mb-4 rounded-md border border-risk-low bg-white px-3 py-2 text-sm text-risk-low-text">
            Account created — sign in below.
          </p>
        )}

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          {mode === 'signup' && (
            <>
              <Input
                label="Invite code"
                required
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value)}
                placeholder="rrinv_..."
              />
              <div className="flex flex-col gap-1.5">
                <label htmlFor="signup-role" className="text-sm font-medium text-slate-900">
                  Role
                </label>
                <select
                  id="signup-role"
                  value={role}
                  onChange={(e) => setRole(e.target.value as typeof role)}
                  className="h-10 rounded-md border border-slate-300 px-3 text-sm text-slate-900 focus:border-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-600"
                >
                  {SELF_SELECTABLE_ROLES.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
              <Input
                label="Name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Your name"
              />
            </>
          )}
          {mode === 'signup-org' && (
            <>
              <Input
                label="Organization name"
                required
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                placeholder="Acme Corp"
              />
              <Input
                label="Your name"
                required
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Jane Admin"
              />
            </>
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
              minLength={mode !== 'login' ? 8 : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode !== 'login' ? 'At least 8 characters' : undefined}
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
          {mode !== 'login' && (
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
            {mode === 'login' ? 'Sign in' : mode === 'signup-org' ? 'Create organization' : 'Create account'}
          </Button>
        </form>

        <p className="mt-4 text-center text-sm text-slate-500">
          {mode === 'login' ? (
            <>
              Have an invite code?{' '}
              <button
                type="button"
                className="font-medium text-primary-600 hover:underline"
                onClick={() => {
                  setMode('signup')
                  setFormError(null)
                  setSignupSuccess(false)
                }}
              >
                Sign up
              </button>
              {' · '}
              New here?{' '}
              <button
                type="button"
                className="font-medium text-primary-600 hover:underline"
                onClick={() => {
                  setMode('signup-org')
                  setFormError(null)
                  setSignupSuccess(false)
                }}
              >
                Create an organization
              </button>
            </>
          ) : (
            <button
              type="button"
              className="font-medium text-primary-600 hover:underline"
              onClick={() => {
                setMode('login')
                setFormError(null)
                setSignupSuccess(false)
                setConfirmPassword('')
                setInviteCode('')
                setOrgName('')
              }}
            >
              Sign in
            </button>
          )}
        </p>
        </Card>
      </div>
    </div>
  )
}
