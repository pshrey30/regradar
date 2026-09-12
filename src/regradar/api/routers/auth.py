"""FE-02 — authentication: Google SSO (/v1/auth/google/login,
/v1/auth/google/callback) and email/password (/v1/auth/signup,
/v1/auth/login), plus /v1/auth/logout for both.

The session cookie's raw value IS a regular API key (generated the same
way `create-api-key` does) — its hash is what api_keys.key_hash stores, so
`get_current_key` (api/deps.py) can authenticate a cookie exactly like an
`Authorization: Bearer` header, through the same RLS/rate-limit machinery,
with no new auth path to keep in sync. Logging in again (either method)
rotates the row's key_hash to a fresh value, which is also what makes a
previous session invalid the moment a new one starts — a deliberate
single-active-session-per-identity simplification for a portfolio
project, not a scalability requirement.

No organization-management surface exists yet (SEC-05's own precedent,
still true) — a first-time signup (either method) is provisioned into the
single, first-created organization, same as `create-api-key`.

Every signup path — email/password AND Google SSO — is invite-gated: both
require a valid, unused code from an Admin-created `invites` row
(api/routers/invites.py). Gating only one path would make the whole
requirement trivially bypassable (anyone could just use the other one),
so `google_login`/`google_callback` carry an invite_code + role through
the OAuth round-trip in a second short-lived cookie, mirroring how
`oauth_state` is already carried. The invite itself is purely a signup
gate — it carries no role. The person signing up chooses their own role,
but only from schemas.auth.SELF_SELECTABLE_ROLES, which excludes Admin;
an actual Admin account is only ever created by another Admin directly
(`create-api-key`), never through self-service signup. An invite is only
ever consumed when it actually creates a NEW account — a returning
Google user logging back in via an existing row never touches the invite
system at all.

Email/password and Google SSO are two independent signup paths into the
same api_keys table, not linked accounts — signing up with the same email
both ways creates two separate rows today. Account linking is real,
legitimate future scope, deliberately not built here: it needs a
decision this ticket has no basis for (which identity "wins" a role/org
conflict) that's better made when there's an actual second real use case
asking for it.

Google SSO is currently disabled (see `_GOOGLE_SSO_ENABLED` below) —
both routes redirect straight to `?error=google_disabled` without
touching Google or the database at all. This is a single-line, reversible
toggle: every real line of SSO logic above (invite-gating through the
OAuth round-trip, the state-cookie CSRF check, session issuance) is
untouched and still fully covered by tests, which enable the flag for
the duration of the test rather than deleting coverage for logic that
still exists and works.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Cookie, Depends, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.deps import AuthenticatedKey
from regradar.api.errors import ApiError
from regradar.api.middleware.rate_limit import enforce_rate_limit, get_authenticated_db
from regradar.core.api_keys import generate_api_key, hash_api_key
from regradar.core.config import get_settings
from regradar.core.db import get_session_factory, set_rls_context
from regradar.core.passwords import WeakPasswordError, hash_password, verify_password
from regradar.core.redis_client import get_redis_client
from regradar.core.sso import (
    GoogleIdentity,
    SsoError,
    build_google_authorize_url,
    exchange_code_for_identity,
    generate_state_token,
)
from regradar.models.api_key import ApiKey
from regradar.models.enums import ApiKeyRole
from regradar.models.invite import Invite
from regradar.models.organization import Organization
from regradar.schemas.auth import (
    SELF_SELECTABLE_ROLES,
    ChangePasswordRequest,
    LoginRequest,
    SignupRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_STATE_COOKIE_NAME = "oauth_state"
_INVITE_COOKIE_NAME = "oauth_invite_code"
_ROLE_COOKIE_NAME = "oauth_role"
_STATE_COOKIE_MAX_AGE_SECONDS = 600  # a login attempt has 10 minutes to complete
_SESSION_COOKIE_NAME = "regradar_session"
_LOGIN_LOCKOUT_THRESHOLD = 5
_LOGIN_LOCKOUT_WINDOW_SECONDS = 15 * 60

_INVALID_LOGIN_ERROR = ApiError(
    status_code=401, code="invalid_credentials", message="Incorrect email or password."
)
_INVALID_INVITE_ERROR = ApiError(
    status_code=422,
    code="invalid_invite_code",
    message="Invalid or already-used invite code.",
)


async def _consume_invite(db: AsyncSession, *, code: str, used_by_email: str) -> None:
    """Atomically validates and consumes an invite code — the `WHERE
    used_at IS NULL` conditional UPDATE is what makes a code single-use
    even under two concurrent signups racing the same code (same pattern
    SEC-04 established for duplicate-filing protection). Raises
    ApiError(422) if the code doesn't exist or was already used; the
    caller decides how to surface that (signup() lets it become the
    request's own error response; the Google flow catches it and turns
    it into a redirect instead, since that flow can't return raw JSON)."""
    code_hash = hash_api_key(code)
    result = await db.execute(
        update(Invite)
        .where(Invite.code_hash == code_hash, Invite.used_at.is_(None))
        .values(used_at=datetime.now(UTC), used_by_email=used_by_email)
        .returning(Invite.id)
    )
    if result.scalar_one_or_none() is None:
        raise _INVALID_INVITE_ERROR


# Google sign-in is temporarily disabled, both routes below — not just the
# frontend button — since a determined caller could still hit these URLs
# directly with the UI entry point gone. Flip back to True to re-enable;
# every other line of the real SSO logic (invite-gating, cookie handling,
# CSRF state check) is untouched and still fully tested, so this is a
# single-line, reversible toggle, not a partial removal to redo later.
_GOOGLE_SSO_ENABLED = False


def _cookie_kwargs(*, max_age: int) -> dict:
    settings = get_settings()
    return {
        "httponly": True,
        # https:// isn't available on plain http://localhost in dev —
        # matches SEC-02's own ENV=development carve-out for the same
        # real constraint.
        "secure": settings.env != "development",
        "samesite": "lax",
        "max_age": max_age,
        "path": "/",
    }


@router.get("/v1/auth/google/login")
async def google_login(
    invite_code: str | None = None, role: ApiKeyRole | None = None
) -> RedirectResponse:
    """`invite_code`/`role` are optional here on purpose: this one link
    serves both a returning user logging back in (no invite needed at
    all — they already have an account) and a first-time signup (invite
    required). This endpoint can't tell which case it is yet — Google
    hasn't identified anyone — so it only pre-validates an invite when
    one was actually supplied (the frontend only supplies one from
    signup mode), and defers the real, mandatory check entirely to
    google_callback -> _find_or_create_api_key, which knows for certain
    whether this is a new row or an existing one."""
    settings = get_settings()
    login_url = f"{settings.frontend_base_url}/login"

    if not _GOOGLE_SSO_ENABLED:
        return RedirectResponse(url=f"{login_url}?error=google_disabled", status_code=307)

    if role is not None and role not in SELF_SELECTABLE_ROLES:
        return RedirectResponse(url=f"{login_url}?error=invalid_role", status_code=307)

    if invite_code is not None:
        # Fail fast, before ever sending the user to Google: a read-only
        # check here so an invalid code shows an error immediately instead
        # of after a full round trip through Google's consent screen. This
        # is NOT the real enforcement point (a code could still be
        # consumed by someone else in the gap between here and the
        # callback) — that happens atomically in _find_or_create_api_key,
        # which re-validates for real.
        session_factory = get_session_factory()
        async with session_factory() as db:
            await set_rls_context(db, role="service")
            code_hash = hash_api_key(invite_code)
            result = await db.execute(
                select(Invite.id).where(Invite.code_hash == code_hash, Invite.used_at.is_(None))
            )
            if result.scalar_one_or_none() is None:
                return RedirectResponse(url=f"{login_url}?error=invalid_invite", status_code=307)

    state = generate_state_token()
    redirect = RedirectResponse(url=build_google_authorize_url(state=state), status_code=307)
    redirect.set_cookie(
        _STATE_COOKIE_NAME, state, **_cookie_kwargs(max_age=_STATE_COOKIE_MAX_AGE_SECONDS)
    )
    # Carried through the OAuth round-trip the same way oauth_state is —
    # google_callback needs both to gate/role a first-time signup exactly
    # like the email/password path does. Only set when actually supplied;
    # a returning-user login leaves these unset, and the callback treats
    # a missing cookie the same as an absent query param (both are None).
    if invite_code is not None:
        redirect.set_cookie(
            _INVITE_COOKIE_NAME, invite_code, **_cookie_kwargs(max_age=_STATE_COOKIE_MAX_AGE_SECONDS)
        )
    if role is not None:
        redirect.set_cookie(
            _ROLE_COOKIE_NAME, role.value, **_cookie_kwargs(max_age=_STATE_COOKIE_MAX_AGE_SECONDS)
        )
    return redirect


async def _find_or_create_api_key(
    identity: GoogleIdentity, *, invite_code: str | None, role: ApiKeyRole | None
) -> str:
    """Returns the new plaintext session token for this identity. Runs as
    `service` — the row this reads/writes doesn't belong to a caller who's
    authenticated yet (that's the whole point of this endpoint).

    Raises ApiError (422, invalid_invite_code) if this would create a NEW
    account and the invite is missing/invalid/already used, or the role
    isn't self-selectable — the caller (google_callback) turns that into
    a redirect. A RETURNING user (an existing row already matches this
    Google identity) never touches the invite system at all — invites
    gate account *creation*, not every future login."""
    session_factory = get_session_factory()
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        result = await db.execute(
            select(ApiKey).where(
                ApiKey.sso_provider == "google", ApiKey.sso_subject_id == identity.subject_id
            )
        )
        key = result.scalar_one_or_none()

        plaintext_token = generate_api_key()
        if key is None:
            if not invite_code or role not in SELF_SELECTABLE_ROLES:
                raise _INVALID_INVITE_ERROR
            await _consume_invite(db, code=invite_code, used_by_email=identity.email)

            org_id = (
                await db.execute(select(Organization.id).order_by(Organization.created_at.asc()).limit(1))
            ).scalar_one()
            key = ApiKey(
                organization_id=org_id,
                owner_label=identity.name or identity.email,
                role=role,
                sso_provider="google",
                sso_subject_id=identity.subject_id,
            )
            db.add(key)

        key.key_hash = hash_api_key(plaintext_token)
        key.is_active = True
        await db.commit()
        return plaintext_token


@router.get("/v1/auth/google/callback")
async def google_callback(
    code: str,
    state: str,
    oauth_state: str | None = Cookie(default=None),
    oauth_invite_code: str | None = Cookie(default=None),
    oauth_role: str | None = Cookie(default=None),
) -> RedirectResponse:
    settings = get_settings()
    login_url = f"{settings.frontend_base_url}/login"

    def _clear_oauth_cookies(response: RedirectResponse) -> None:
        response.delete_cookie(_STATE_COOKIE_NAME, path="/")
        response.delete_cookie(_INVITE_COOKIE_NAME, path="/")
        response.delete_cookie(_ROLE_COOKIE_NAME, path="/")

    if not _GOOGLE_SSO_ENABLED:
        redirect = RedirectResponse(url=f"{login_url}?error=google_disabled", status_code=307)
        _clear_oauth_cookies(redirect)
        return redirect

    # CSRF check: the state param Google echoed back must match the value
    # this exact browser was given at /v1/auth/google/login — anyone else
    # driving the browser through this callback (the attack this defends
    # against) never had that cookie set.
    if not oauth_state or oauth_state != state:
        logger.warning("Google OAuth callback state mismatch — rejecting")
        return RedirectResponse(url=f"{login_url}?error=state_mismatch", status_code=307)

    try:
        identity = await exchange_code_for_identity(code=code)
    except SsoError as exc:
        logger.warning("Google OAuth callback failed: %s", exc)
        return RedirectResponse(url=f"{login_url}?error=sso_failed", status_code=307)

    try:
        session_token = await _find_or_create_api_key(
            identity,
            invite_code=oauth_invite_code,
            role=ApiKeyRole(oauth_role) if oauth_role else None,
        )
    except ApiError:
        redirect = RedirectResponse(url=f"{login_url}?error=invalid_invite", status_code=307)
        _clear_oauth_cookies(redirect)
        return redirect

    redirect = RedirectResponse(url=settings.frontend_base_url, status_code=307)
    _clear_oauth_cookies(redirect)
    redirect.set_cookie(
        _SESSION_COOKIE_NAME,
        session_token,
        **_cookie_kwargs(max_age=settings.session_cookie_max_age_seconds),
    )
    return redirect


@router.post("/v1/auth/logout")
async def logout(
    regradar_session: str | None = Cookie(default=None),
) -> RedirectResponse:
    settings = get_settings()
    if regradar_session:
        # Rotates key_hash to a value derived from a fresh random token
        # that was never issued to anyone, so the cookie the browser just
        # had (if it leaked before this request) can't be replayed after
        # logout — clearing the cookie alone only stops *this* browser
        # from presenting it again, not a copy an attacker already has.
        session_factory = get_session_factory()
        async with session_factory() as db:
            await set_rls_context(db, role="service")
            key_hash = hash_api_key(regradar_session)
            result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
            key = result.scalar_one_or_none()
            if key is not None:
                key.key_hash = hash_api_key(generate_api_key())
                await db.commit()

    # 303 (See Other), not 307 — this redirect follows a POST, and 307
    # deliberately preserves the original method on redirect, so the
    # browser would re-issue this as a POST to /login. The frontend's
    # dev server (and most static hosts) only serve the SPA's index.html
    # fallback for GET requests, so a method-preserving redirect here
    # produced a genuine 404 instead of the login page. 303 is the
    # correct status for the standard Post/Redirect/Get pattern — it
    # always converts to GET on the client, regardless of the original
    # method.
    redirect = RedirectResponse(url=f"{settings.frontend_base_url}/login", status_code=303)
    redirect.delete_cookie(_SESSION_COOKIE_NAME, path="/")
    return redirect


def _login_lockout_key(email: str) -> str:
    return f"login_failures:{email}"


async def _is_locked_out(email: str) -> bool:
    try:
        client = get_redis_client()
        count = await client.get(_login_lockout_key(email))
        return count is not None and int(count) >= _LOGIN_LOCKOUT_THRESHOLD
    except Exception:
        # Matches rate_limit.py's own fail-open convention: a Redis outage
        # is a real, transient infra failure, not a reason to lock every
        # login attempt out — brute-force protection is best-effort on top
        # of the real (still-enforced) password check, not the only guard.
        logger.warning("Login lockout check failed; failing open.", exc_info=True)
        return False


async def _record_login_failure(email: str) -> None:
    try:
        client = get_redis_client()
        key = _login_lockout_key(email)
        await client.incr(key)
        await client.expire(key, _LOGIN_LOCKOUT_WINDOW_SECONDS)
    except Exception:
        logger.warning("Recording login failure count failed; ignoring.", exc_info=True)


async def _reset_login_failures(email: str) -> None:
    try:
        await get_redis_client().delete(_login_lockout_key(email))
    except Exception:
        logger.warning("Resetting login failure count failed; ignoring.", exc_info=True)


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        _SESSION_COOKIE_NAME, token, **_cookie_kwargs(max_age=settings.session_cookie_max_age_seconds)
    )


@router.post("/v1/auth/signup", status_code=201)
async def signup(payload: SignupRequest) -> Response:
    """Creates the account only — deliberately does not establish a
    session. Signing up and signing in are two separate actions from the
    user's point of view (the frontend sends them to the login screen
    with a "account created" message afterward), so this endpoint
    shouldn't silently log them in behind that framing."""
    try:
        password_hash = hash_password(payload.password)
    except WeakPasswordError as exc:
        raise ApiError(status_code=422, code="weak_password", message=str(exc)) from exc

    session_factory = get_session_factory()
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        existing = await db.execute(select(ApiKey.id).where(ApiKey.email == payload.email))
        if existing.scalar_one_or_none() is not None:
            raise ApiError(
                status_code=409,
                code="email_taken",
                message="An account with this email already exists.",
            )

        # Consumed only after the email_taken check, and inside the same
        # transaction as the row this creates — a failed attempt (e.g. the
        # email really was taken) never burns a real invite, and if
        # anything below fails, the whole transaction (including this
        # UPDATE) rolls back together.
        await _consume_invite(db, code=payload.invite_code, used_by_email=payload.email)

        org_id = (
            await db.execute(select(Organization.id).order_by(Organization.created_at.asc()).limit(1))
        ).scalar_one()
        key = ApiKey(
            organization_id=org_id,
            owner_label=payload.display_name or payload.email,
            role=payload.role,
            email=payload.email,
            password_hash=password_hash,
            # A row's key_hash column is required and unique even though
            # nothing is ever meant to authenticate with this particular
            # value — login() rotates it to a real session token on the
            # first actual sign-in, same as every other login already does.
            key_hash=hash_api_key(generate_api_key()),
        )
        db.add(key)
        await db.commit()

    return JSONResponse(status_code=201, content={"status": "ok"})


@router.post("/v1/auth/login")
async def login(payload: LoginRequest) -> Response:
    # Locked out before ever touching the DB — the whole point is to stop
    # a brute-force loop from getting unlimited guesses, including against
    # an email that was never registered.
    if await _is_locked_out(payload.email):
        raise ApiError(
            status_code=429,
            code="too_many_attempts",
            message="Too many failed login attempts. Try again later.",
        )

    session_factory = get_session_factory()
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        result = await db.execute(
            select(ApiKey).where(ApiKey.email == payload.email, ApiKey.password_hash.is_not(None))
        )
        key = result.scalar_one_or_none()

        # Deliberately the same error whether the email doesn't exist, the
        # account is inactive, or the password is wrong — matching
        # get_current_key's own "don't let the response distinguish
        # cases" convention, so a caller can't enumerate registered
        # emails by probing this endpoint.
        if key is None or not key.is_active:
            await _record_login_failure(payload.email)
            raise _INVALID_LOGIN_ERROR
        # The query's password_hash IS NOT NULL filter already guarantees
        # this, narrowing str | None -> str for mypy.
        assert key.password_hash is not None
        if not verify_password(payload.password, key.password_hash):
            await _record_login_failure(payload.email)
            raise _INVALID_LOGIN_ERROR

        await _reset_login_failures(payload.email)
        plaintext_token = generate_api_key()
        key.key_hash = hash_api_key(plaintext_token)
        await db.commit()

    response = JSONResponse(content={"status": "ok"})
    _set_session_cookie(response, plaintext_token)
    return response


@router.post("/v1/auth/change-password", status_code=204)
async def change_password(
    body: ChangePasswordRequest,
    caller: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> Response:
    row = await db.get(ApiKey, caller.id)
    assert row is not None  # the row that authenticated this request

    if row.password_hash is None:
        # A Google-SSO-only account has no password to change — signing
        # in with a password was never how this identity authenticates.
        raise ApiError(
            status_code=400,
            code="no_password_set",
            message="This account signs in with Google and has no password to change.",
        )
    if not verify_password(body.current_password, row.password_hash):
        raise ApiError(
            status_code=401, code="incorrect_password", message="Current password is incorrect."
        )

    try:
        row.password_hash = hash_password(body.new_password)
    except WeakPasswordError as exc:
        raise ApiError(status_code=422, code="weak_password", message=str(exc)) from exc

    await db.commit()
    return Response(status_code=204)
