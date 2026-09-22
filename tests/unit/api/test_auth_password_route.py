"""Unit tests for FE-02's email/password signup/login routes — no real
network/DB calls. Route functions are called directly, matching this
project's established convention for routes with Cookie()-typed params
elsewhere in auth.py.
"""

import os

os.environ.setdefault("APP_SECRET_KEY", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_BUCKET_NAME", "test-bucket")
os.environ.setdefault("S3_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("HUGGINGFACE_API_TOKEN", "test")
os.environ.setdefault("SEC_EDGAR_USER_AGENT", "RegRadar/1.0 (test@example.com)")

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from regradar.api.errors import ApiError
from regradar.api.routers import auth as auth_module
from regradar.core.passwords import hash_password
from regradar.models.api_key import ApiKey
from regradar.models.enums import ApiKeyRole
from regradar.models.organization import Organization
from regradar.schemas.auth import LoginRequest, SignupOrgRequest, SignupRequest

_VALID_SIGNUP_KWARGS = {"invite_code": "rrinv_test-code", "role": ApiKeyRole.ANALYST}


def _mock_row(*, email: str, password: str, is_active: bool = True) -> MagicMock:
    row = MagicMock()
    row.id = uuid4()
    row.email = email
    row.password_hash = hash_password(password)
    row.is_active = is_active
    row.key_hash = "old-hash"
    return row


def _patch_db(monkeypatch: pytest.MonkeyPatch, *, found_row=None, org_id=None):
    mock_db = AsyncMock()

    lookup_result = MagicMock()
    lookup_result.scalar_one_or_none = MagicMock(return_value=found_row)
    org_result = MagicMock()
    org_result.scalar_one = MagicMock(return_value=org_id or uuid4())
    real_results = iter([lookup_result, org_result] if org_id is not None or found_row is None else [lookup_result])

    async def _execute(stmt, *args, **kwargs):
        if "set_config" in getattr(stmt, "text", ""):
            return MagicMock()
        return next(real_results)

    mock_db.execute = AsyncMock(side_effect=_execute)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(auth_module, "get_session_factory", lambda: mock_session_factory)
    return mock_db


def _patch_signup_db(
    monkeypatch: pytest.MonkeyPatch,
    *,
    existing_email_row=None,
    invite_valid: bool = True,
    org_id=None,
):
    """Sequences the three queries signup() issues in order: the
    email-uniqueness check, the invite-consuming UPDATE...RETURNING, and
    (only if both of those pass) the org lookup — matching exactly what
    the real route does, so a test can't accidentally pass by mocking
    queries out of order."""
    mock_db = AsyncMock()

    email_result = MagicMock()
    email_result.scalar_one_or_none = MagicMock(return_value=existing_email_row)

    invite_result = MagicMock()
    invite_result.scalar_one_or_none = MagicMock(return_value=uuid4() if invite_valid else None)

    org_result = MagicMock()
    org_result.scalar_one = MagicMock(return_value=org_id or uuid4())

    if existing_email_row is not None:
        queue = [email_result]
    elif not invite_valid:
        queue = [email_result, invite_result]
    else:
        queue = [email_result, invite_result, org_result]
    real_results = iter(queue)

    async def _execute(stmt, *args, **kwargs):
        if "set_config" in getattr(stmt, "text", ""):
            return MagicMock()
        return next(real_results)

    mock_db.execute = AsyncMock(side_effect=_execute)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(auth_module, "get_session_factory", lambda: mock_session_factory)
    return mock_db


@pytest.fixture(autouse=True)
def _mock_redis(monkeypatch: pytest.MonkeyPatch):
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.incr = AsyncMock(return_value=1)
    client.expire = AsyncMock()
    client.delete = AsyncMock()
    monkeypatch.setattr(auth_module, "get_redis_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_signup_creates_account_without_logging_in(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_db = _patch_signup_db(monkeypatch, org_id=uuid4())

    response = await auth_module.signup(
        SignupRequest(
            email="new@example.com",
            password="a-real-password",
            display_name="New Person",
            **_VALID_SIGNUP_KWARGS,
        )
    )

    assert response.status_code == 201
    mock_db.add.assert_called_once()
    created = mock_db.add.call_args.args[0]
    assert created.email == "new@example.com"
    assert created.role == ApiKeyRole.ANALYST
    assert created.password_hash != "a-real-password"  # never stored in plaintext
    # Signing up creates the account only — it's a separate, explicit
    # action from signing in, so no session cookie is set here.
    assert "regradar_session=" not in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_signup_uses_the_chosen_self_selectable_role(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_db = _patch_signup_db(monkeypatch, org_id=uuid4())

    await auth_module.signup(
        SignupRequest(
            email="new@example.com",
            password="a-real-password",
            invite_code="rrinv_test-code",
            role=ApiKeyRole.LEGAL_COUNSEL,
        )
    )

    created = mock_db.add.call_args.args[0]
    assert created.role == ApiKeyRole.LEGAL_COUNSEL


def test_signup_request_rejects_admin_role() -> None:
    with pytest.raises(Exception, match="Not a role you can sign up as"):
        SignupRequest(
            email="new@example.com",
            password="a-real-password",
            invite_code="rrinv_test-code",
            role=ApiKeyRole.ADMIN,
        )


@pytest.mark.asyncio
async def test_signup_rejects_duplicate_email(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _mock_row(email="taken@example.com", password="whatever123")
    mock_db = _patch_signup_db(monkeypatch, existing_email_row=existing)

    with pytest.raises(ApiError) as exc_info:
        await auth_module.signup(
            SignupRequest(email="taken@example.com", password="a-real-password", **_VALID_SIGNUP_KWARGS)
        )

    assert exc_info.value.status_code == 409
    mock_db.add.assert_not_called()


@pytest.mark.asyncio
async def test_signup_rejects_invalid_invite_code(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_db = _patch_signup_db(monkeypatch, invite_valid=False)

    with pytest.raises(ApiError) as exc_info:
        await auth_module.signup(
            SignupRequest(email="new@example.com", password="a-real-password", **_VALID_SIGNUP_KWARGS)
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "invalid_invite_code"
    mock_db.add.assert_not_called()


@pytest.mark.asyncio
async def test_signup_rejects_weak_password() -> None:
    with pytest.raises(ApiError) as exc_info:
        await auth_module.signup(
            SignupRequest(email="new@example.com", password="short", **_VALID_SIGNUP_KWARGS)
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_login_succeeds_with_correct_password(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _mock_row(email="user@example.com", password="correct-password")
    mock_db = _patch_db(monkeypatch, found_row=row)

    response = await auth_module.login(LoginRequest(email="user@example.com", password="correct-password"))

    assert response.status_code == 200
    assert row.key_hash != "old-hash"
    assert "regradar_session=" in response.headers.get("set-cookie", "")
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_rejects_wrong_password_and_records_failure(
    monkeypatch: pytest.MonkeyPatch, _mock_redis: AsyncMock
) -> None:
    row = _mock_row(email="user@example.com", password="correct-password")
    _patch_db(monkeypatch, found_row=row)

    with pytest.raises(ApiError) as exc_info:
        await auth_module.login(LoginRequest(email="user@example.com", password="wrong-password"))

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "invalid_credentials"
    _mock_redis.incr.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_rejects_unknown_email_with_same_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No account-enumeration signal: identical error/status to a wrong
    password on a real account."""
    _patch_db(monkeypatch, found_row=None)

    with pytest.raises(ApiError) as exc_info:
        await auth_module.login(LoginRequest(email="nobody@example.com", password="whatever123"))

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "invalid_credentials"


@pytest.mark.asyncio
async def test_login_locked_out_after_threshold(_mock_redis: AsyncMock) -> None:
    _mock_redis.get = AsyncMock(return_value=b"5")

    with (
        patch.object(auth_module, "get_session_factory") as mock_get_session_factory,
        pytest.raises(ApiError) as exc_info,
    ):
        await auth_module.login(LoginRequest(email="user@example.com", password="whatever123"))

    assert exc_info.value.status_code == 429
    mock_get_session_factory.assert_not_called()  # locked out before ever touching the DB


@pytest.mark.asyncio
async def test_login_succeeds_when_redis_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, _mock_redis: AsyncMock
) -> None:
    """A Redis outage must degrade login (skip brute-force lockout
    tracking), never break it outright — the real password check is
    still the actual security boundary."""
    _mock_redis.get = AsyncMock(side_effect=ConnectionError("Connection refused"))
    _mock_redis.incr = AsyncMock(side_effect=ConnectionError("Connection refused"))
    _mock_redis.delete = AsyncMock(side_effect=ConnectionError("Connection refused"))
    row = _mock_row(email="user@example.com", password="correct-password")
    _patch_db(monkeypatch, found_row=row)

    response = await auth_module.login(LoginRequest(email="user@example.com", password="correct-password"))

    assert response.status_code == 200
    assert "regradar_session=" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_login_resets_failure_count_on_success(
    monkeypatch: pytest.MonkeyPatch, _mock_redis: AsyncMock
) -> None:
    row = _mock_row(email="user@example.com", password="correct-password")
    _patch_db(monkeypatch, found_row=row)

    await auth_module.login(LoginRequest(email="user@example.com", password="correct-password"))

    _mock_redis.delete.assert_awaited_once()


def _patch_signup_org_db(
    monkeypatch: pytest.MonkeyPatch, *, existing_email_row=None
):
    """Sequences signup_org()'s one query (email-uniqueness check) — no
    invite consumption, no org lookup (a NEW org is created, never
    looked up)."""
    mock_db = AsyncMock()

    email_result = MagicMock()
    email_result.scalar_one_or_none = MagicMock(return_value=existing_email_row)
    mock_db.execute = AsyncMock(return_value=email_result)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(auth_module, "get_session_factory", lambda: mock_session_factory)
    return mock_db


@pytest.mark.asyncio
async def test_signup_org_creates_organization_and_admin_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_db = _patch_signup_org_db(monkeypatch)

    response = await auth_module.signup_org(
        SignupOrgRequest(
            org_name="Acme Corp",
            display_name="Jane Admin",
            email="jane@acme.example",
            password="correct horse battery staple",
        )
    )

    assert response.status_code == 201
    added_rows = [call.args[0] for call in mock_db.add.call_args_list]
    orgs = [row for row in added_rows if isinstance(row, Organization)]
    keys = [row for row in added_rows if isinstance(row, ApiKey)]
    assert len(orgs) == 1
    assert orgs[0].name == "Acme Corp"
    assert len(keys) == 1
    assert keys[0].role == ApiKeyRole.ADMIN
    assert keys[0].email == "jane@acme.example"
    assert keys[0].owner_label == "Jane Admin"
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_signup_org_rejects_duplicate_email(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = MagicMock()
    _patch_signup_org_db(monkeypatch, existing_email_row=existing)

    with pytest.raises(ApiError) as exc_info:
        await auth_module.signup_org(
            SignupOrgRequest(
                org_name="Acme Corp",
                display_name="Jane Admin",
                email="jane@acme.example",
                password="correct horse battery staple",
            )
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "email_taken"


@pytest.mark.asyncio
async def test_signup_org_rejects_weak_password(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_signup_org_db(monkeypatch)

    with pytest.raises(ApiError) as exc_info:
        await auth_module.signup_org(
            SignupOrgRequest(
                org_name="Acme Corp",
                display_name="Jane Admin",
                email="jane@acme.example",
                password="weak",
            )
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "weak_password"


def test_signup_org_request_rejects_empty_org_name() -> None:
    with pytest.raises(ValueError):
        SignupOrgRequest(
            org_name="",
            display_name="Jane Admin",
            email="jane@acme.example",
            password="correct horse battery staple",
        )
