"""Unit tests for FE-02's Google SSO routes — no real network/Google calls.

Route functions are called directly (matching test_deps.py's convention),
not through TestClient, since Cookie(default=None) resolves to FastAPI's
own marker object rather than None when called outside real request
handling — every call below passes cookie params explicitly for that
reason.
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
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from regradar.api.routers import auth as auth_module
from regradar.core.sso import GoogleIdentity, SsoError
from regradar.models.enums import ApiKeyRole


def _mock_row(*, sso_subject_id: str | None = None) -> MagicMock:
    row = MagicMock()
    row.id = uuid4()
    row.key_hash = "old-hash"
    row.is_active = True
    row.sso_provider = "google" if sso_subject_id else None
    row.sso_subject_id = sso_subject_id
    return row


def _patch_db(monkeypatch: pytest.MonkeyPatch, *, found_row=None, org_id=None):
    mock_db = AsyncMock()

    key_result = MagicMock()
    key_result.scalar_one_or_none = MagicMock(return_value=found_row)
    org_result = MagicMock()
    org_result.scalar_one = MagicMock(return_value=org_id or uuid4())
    real_results = iter([key_result, org_result] if found_row is None else [key_result])

    async def _execute(stmt, *args, **kwargs):
        # set_rls_context issues its own set_config calls before every real
        # query in this module — those aren't real queries and mustn't
        # consume the real_results queue (see delivery_agent's own fix for
        # the same underlying issue).
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


@pytest.mark.asyncio
async def test_google_login_redirects_and_sets_state_cookie() -> None:
    response = await auth_module.google_login()

    assert response.status_code == 307
    assert "accounts.google.com" in response.headers["location"]
    assert "oauth_state=" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_google_callback_rejects_missing_state_cookie() -> None:
    response = await auth_module.google_callback(code="abc", state="xyz", oauth_state=None)

    assert response.status_code == 307
    assert "error=state_mismatch" in response.headers["location"]


@pytest.mark.asyncio
async def test_google_callback_rejects_mismatched_state() -> None:
    response = await auth_module.google_callback(code="abc", state="xyz", oauth_state="different")

    assert response.status_code == 307
    assert "error=state_mismatch" in response.headers["location"]


@pytest.mark.asyncio
async def test_google_callback_redirects_on_sso_error() -> None:
    with patch.object(
        auth_module, "exchange_code_for_identity", new=AsyncMock(side_effect=SsoError("boom"))
    ):
        response = await auth_module.google_callback(code="abc", state="xyz", oauth_state="xyz")

    assert response.status_code == 307
    assert "error=sso_failed" in response.headers["location"]


@pytest.mark.asyncio
async def test_google_callback_success_sets_session_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_db(monkeypatch, found_row=None)
    identity = GoogleIdentity(
        subject_id="google-sub-123", email="user@example.com", email_verified=True, name="Test User"
    )

    with patch.object(auth_module, "exchange_code_for_identity", new=AsyncMock(return_value=identity)):
        response = await auth_module.google_callback(code="abc", state="xyz", oauth_state="xyz")

    assert response.status_code == 307
    # This response sets two cookies (deletes oauth_state, sets
    # regradar_session) — .headers.get() only returns the first Set-Cookie
    # value, so check every raw header instead.
    set_cookie_headers = [
        v.decode() for k, v in response.raw_headers if k.decode().lower() == "set-cookie"
    ]
    session_cookie = next(c for c in set_cookie_headers if c.startswith("regradar_session="))
    assert "HttpOnly" in session_cookie
    assert "samesite=lax" in session_cookie.lower()


@pytest.mark.asyncio
async def test_find_or_create_creates_new_analyst_key_for_unknown_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_db = _patch_db(monkeypatch, found_row=None)
    identity = GoogleIdentity(
        subject_id="google-sub-new", email="new@example.com", email_verified=True, name="New Person"
    )

    await auth_module._find_or_create_api_key(identity)

    mock_db.add.assert_called_once()
    created = mock_db.add.call_args.args[0]
    assert created.role == ApiKeyRole.ANALYST
    assert created.sso_provider == "google"
    assert created.sso_subject_id == "google-sub-new"


@pytest.mark.asyncio
async def test_find_or_create_reuses_existing_row_for_known_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _mock_row(sso_subject_id="google-sub-existing")
    mock_db = _patch_db(monkeypatch, found_row=existing)
    identity = GoogleIdentity(
        subject_id="google-sub-existing", email="known@example.com", email_verified=True, name="Known"
    )

    token = await auth_module._find_or_create_api_key(identity)

    mock_db.add.assert_not_called()  # no new row — the existing one's key_hash was rotated instead
    assert existing.key_hash != "old-hash"
    assert isinstance(token, str) and token


@pytest.mark.asyncio
async def test_logout_clears_cookie() -> None:
    response = await auth_module.logout(regradar_session=None)

    assert response.status_code == 307
    assert "regradar_session=" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_logout_rotates_key_hash_when_session_present(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _mock_row()
    mock_db = _patch_db(monkeypatch, found_row=row)

    await auth_module.logout(regradar_session="rr_some-session-token")

    assert row.key_hash != "old-hash"
    mock_db.commit.assert_awaited_once()
