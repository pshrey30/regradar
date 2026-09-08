// FE-02: every call goes through here so the 401-redirect behavior lives
// in exactly one place — a route calling `fetch` directly would bypass it.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number
  code: string | null

  constructor(message: string, status: number, code: string | null = null) {
    super(message)
    this.status = status
    this.code = code
  }
}

/** Fired on any 401 — main.tsx wires this to a real navigation once, so
 * this module doesn't need to import react-router itself. */
let onUnauthorized: (() => void) | null = null
export function setOnUnauthorized(handler: () => void) {
  onUnauthorized = handler
}

interface ApiFetchOptions extends RequestInit {
  // A 401 from the login/signup endpoints themselves (wrong password, not
  // an expired session) shouldn't trigger the global "redirect to login"
  // side effect — the caller is already there and wants to show its own
  // inline error instead.
  skipAuthRedirect?: boolean
}

export async function apiFetch<T>(path: string, init?: ApiFetchOptions): Promise<T> {
  const { skipAuthRedirect, ...fetchInit } = init ?? {}
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...fetchInit,
    // The session cookie is HttpOnly — JS never reads it, but the browser
    // still needs telling to send it on this cross-origin (dev: :5173 to
    // :8000) request.
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...fetchInit.headers },
  })

  if (response.status === 401 && !skipAuthRedirect) {
    onUnauthorized?.()
  }

  if (!response.ok) {
    // Matches api/errors.py's {"error": {"code", "message", "request_id"}}
    // envelope — falls back to a generic message if the body isn't that
    // shape (e.g. a raw 502 from something in front of the API).
    let message = `Request to ${path} failed with ${response.status}`
    let code: string | null = null
    try {
      const body = await response.json()
      if (body?.error?.message) message = body.error.message
      if (body?.error?.code) code = body.error.code
    } catch {
      // Non-JSON error body — keep the generic message above.
    }
    throw new ApiError(message, response.status, code)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}
