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
from regradar.models.enums import ApiKeyRole
from regradar.schemas.auth import LoginRequest, SignupRequest


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
async def test_signup_creates_account_and_sets_session_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_db = _patch_db(monkeypatch, found_row=None, org_id=uuid4())

    response = await auth_module.signup(
        SignupRequest(email="new@example.com", password="a-real-password", display_name="New Person")
    )

    assert response.status_code == 201
    mock_db.add.assert_called_once()
    created = mock_db.add.call_args.args[0]
    assert created.email == "new@example.com"
    assert created.role == ApiKeyRole.ANALYST
    assert created.password_hash != "a-real-password"  # never stored in plaintext
    assert "regradar_session=" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_signup_rejects_duplicate_email(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _mock_row(email="taken@example.com", password="whatever123")
    mock_db = _patch_db(monkeypatch, found_row=existing)

    with pytest.raises(ApiError) as exc_info:
        await auth_module.signup(SignupRequest(email="taken@example.com", password="a-real-password"))

    assert exc_info.value.status_code == 409
    mock_db.add.assert_not_called()


@pytest.mark.asyncio
async def test_signup_rejects_weak_password() -> None:
    with pytest.raises(ApiError) as exc_info:
        await auth_module.signup(SignupRequest(email="new@example.com", password="short"))

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
async def test_login_resets_failure_count_on_success(
    monkeypatch: pytest.MonkeyPatch, _mock_redis: AsyncMock
) -> None:
    row = _mock_row(email="user@example.com", password="correct-password")
    _patch_db(monkeypatch, found_row=row)

    await auth_module.login(LoginRequest(email="user@example.com", password="correct-password"))

    _mock_redis.delete.assert_awaited_once()
