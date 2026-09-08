"""FE-02 — the actual Google OIDC protocol calls (authorize URL, token
exchange, ID token verification). Kept separate from api/routers/auth.py
so the HTTP-with-Google logic isn't tangled with cookie/DB/redirect
concerns — this module makes no assumptions about FastAPI or the DB.
"""

import secrets
from dataclasses import dataclass

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from regradar.core.config import get_settings

_GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_SCOPES = "openid email profile"


class SsoError(Exception):
    """Any failure in the Google OAuth exchange or ID token verification —
    the callback route turns this into a redirect back to the frontend
    with an error, never a raw 500."""


def generate_state_token() -> str:
    """A random, unguessable value bound to one login attempt via a
    short-lived cookie — the standard OAuth CSRF-protection mechanism.
    Without it, an attacker could trick a victim's browser into
    completing an attacker-initiated login (session fixation)."""
    return secrets.token_urlsafe(32)


def build_google_authorize_url(*, state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": _GOOGLE_SCOPES,
        "state": state,
        # Always show the account chooser rather than silently reusing
        # whatever Google session happens to already be active in the
        # browser — the safer default for a login button.
        "prompt": "select_account",
    }
    return f"{_GOOGLE_AUTHORIZE_URL}?{httpx.QueryParams(params)}"


@dataclass(frozen=True)
class GoogleIdentity:
    subject_id: str
    email: str
    email_verified: bool
    name: str | None


async def exchange_code_for_identity(*, code: str) -> GoogleIdentity:
    """Exchanges an authorization code for tokens, then verifies the ID
    token's signature against Google's real public keys (not just
    base64-decoding the payload — that would trust unverified claims)."""
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise SsoError("Google OAuth is not configured (GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET unset)")

    payload = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret.get_secret_value(),
        "redirect_uri": settings.google_oauth_redirect_uri,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(_GOOGLE_TOKEN_URL, data=payload)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SsoError(f"Google token exchange failed: {exc}") from exc

    token_response = response.json()
    id_token_jwt = token_response.get("id_token")
    if not id_token_jwt:
        raise SsoError("Google token response had no id_token")

    try:
        claims = google_id_token.verify_oauth2_token(
            id_token_jwt, google_requests.Request(), audience=settings.google_client_id
        )
    except ValueError as exc:
        raise SsoError(f"Google ID token verification failed: {exc}") from exc

    email = claims.get("email")
    subject_id = claims.get("sub")
    if not email or not subject_id:
        raise SsoError("Google ID token missing required email/sub claims")

    return GoogleIdentity(
        subject_id=subject_id,
        email=email,
        email_verified=bool(claims.get("email_verified", False)),
        name=claims.get("name"),
    )
