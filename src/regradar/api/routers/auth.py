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
single, first-created organization, same as `create-api-key`. It's
assigned ApiKeyRole.ANALYST, not Admin: self-service sign-in has no
invitation step, so defaulting a new identity to a low-privilege role is
the safe choice — an actual Admin would need to be granted via
`create-api-key` directly, same as today.

Email/password and Google SSO are two independent signup paths into the
same api_keys table, not linked accounts — signing up with the same email
both ways creates two separate rows today. Account linking is real,
legitimate future scope, deliberately not built here: it needs a
decision this ticket has no basis for (which identity "wins" a role/org
conflict) that's better made when there's an actual second real use case
asking for it.
"""

import logging

from fastapi import APIRouter, Cookie, Depends, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
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
from regradar.models.organization import Organization
from regradar.schemas.auth import ChangePasswordRequest, LoginRequest, SignupRequest

logger = logging.getLogger(__name__)

router = APIRouter()

_STATE_COOKIE_NAME = "oauth_state"
_STATE_COOKIE_MAX_AGE_SECONDS = 600  # a login attempt has 10 minutes to complete
_SESSION_COOKIE_NAME = "regradar_session"
_LOGIN_LOCKOUT_THRESHOLD = 5
_LOGIN_LOCKOUT_WINDOW_SECONDS = 15 * 60

_INVALID_LOGIN_ERROR = ApiError(
    status_code=401, code="invalid_credentials", message="Incorrect email or password."
)


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
async def google_login() -> RedirectResponse:
    state = generate_state_token()
    redirect = RedirectResponse(url=build_google_authorize_url(state=state), status_code=307)
    redirect.set_cookie(
        _STATE_COOKIE_NAME, state, **_cookie_kwargs(max_age=_STATE_COOKIE_MAX_AGE_SECONDS)
    )
    return redirect


async def _find_or_create_api_key(identity: GoogleIdentity) -> str:
    """Returns the new plaintext session token for this identity. Runs as
    `service` — the row this reads/writes doesn't belong to a caller who's
    authenticated yet (that's the whole point of this endpoint)."""
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
            org_id = (
                await db.execute(select(Organization.id).order_by(Organization.created_at.asc()).limit(1))
            ).scalar_one()
            key = ApiKey(
                organization_id=org_id,
                owner_label=identity.name or identity.email,
                role=ApiKeyRole.ANALYST,
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
) -> RedirectResponse:
    settings = get_settings()
    login_url = f"{settings.frontend_base_url}/login"

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

    session_token = await _find_or_create_api_key(identity)

    redirect = RedirectResponse(url=settings.frontend_base_url, status_code=307)
    redirect.delete_cookie(_STATE_COOKIE_NAME, path="/")
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

    redirect = RedirectResponse(url=f"{settings.frontend_base_url}/login", status_code=307)
    redirect.delete_cookie(_SESSION_COOKIE_NAME, path="/")
    return redirect


def _login_lockout_key(email: str) -> str:
    return f"login_failures:{email}"


async def _is_locked_out(email: str) -> bool:
    client = get_redis_client()
    count = await client.get(_login_lockout_key(email))
    return count is not None and int(count) >= _LOGIN_LOCKOUT_THRESHOLD


async def _record_login_failure(email: str) -> None:
    client = get_redis_client()
    key = _login_lockout_key(email)
    await client.incr(key)
    await client.expire(key, _LOGIN_LOCKOUT_WINDOW_SECONDS)


async def _reset_login_failures(email: str) -> None:
    await get_redis_client().delete(_login_lockout_key(email))


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        _SESSION_COOKIE_NAME, token, **_cookie_kwargs(max_age=settings.session_cookie_max_age_seconds)
    )


@router.post("/v1/auth/signup", status_code=201)
async def signup(payload: SignupRequest) -> Response:
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

        org_id = (
            await db.execute(select(Organization.id).order_by(Organization.created_at.asc()).limit(1))
        ).scalar_one()
        plaintext_token = generate_api_key()
        key = ApiKey(
            organization_id=org_id,
            owner_label=payload.display_name or payload.email,
            role=ApiKeyRole.ANALYST,
            email=payload.email,
            password_hash=password_hash,
            key_hash=hash_api_key(plaintext_token),
        )
        db.add(key)
        await db.commit()

    response = JSONResponse(status_code=201, content={"status": "ok"})
    _set_session_cookie(response, plaintext_token)
    return response


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
