"""Tests for the API key authentication dependency."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from regradar.api import deps as deps_module
from regradar.api.errors import ApiError
from regradar.models.enums import ApiKeyRole


def _mock_row(*, is_active: bool = True, role: ApiKeyRole = ApiKeyRole.ADMIN):
    row = MagicMock()
    row.id = uuid4()
    row.role = role
    row.owner_label = "test-owner"
    row.rate_limit_per_minute = 60
    row.is_active = is_active
    return row


def _patch_db(monkeypatch: pytest.MonkeyPatch, *, found_row=None):
    mock_db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=found_row)
    mock_db.execute = AsyncMock(return_value=result)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(deps_module, "get_session_factory", lambda: mock_session_factory)
    return mock_db


async def test_missing_header_returns_401():
    with pytest.raises(ApiError) as exc_info:
        await deps_module.get_current_key(authorization="")

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "invalid_api_key"


async def test_malformed_header_returns_401():
    with pytest.raises(ApiError) as exc_info:
        await deps_module.get_current_key(authorization="not-bearer-format")

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "invalid_api_key"


async def test_unknown_key_returns_401(monkeypatch: pytest.MonkeyPatch):
    _patch_db(monkeypatch, found_row=None)

    with pytest.raises(ApiError) as exc_info:
        await deps_module.get_current_key(authorization="Bearer rr_unknown-key")

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "invalid_api_key"


async def test_revoked_key_returns_401(monkeypatch: pytest.MonkeyPatch):
    _patch_db(monkeypatch, found_row=_mock_row(is_active=False))

    with pytest.raises(ApiError) as exc_info:
        await deps_module.get_current_key(authorization="Bearer rr_revoked-key")

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "invalid_api_key"


async def test_valid_active_key_returns_authenticated_key(monkeypatch: pytest.MonkeyPatch):
    row = _mock_row(is_active=True, role=ApiKeyRole.ANALYST)
    _patch_db(monkeypatch, found_row=row)

    result = await deps_module.get_current_key(authorization="Bearer rr_valid-key")

    assert result.id == row.id
    assert result.role == ApiKeyRole.ANALYST
    assert result.owner_label == "test-owner"
    assert result.rate_limit_per_minute == 60


async def test_valid_key_updates_last_used_at(monkeypatch: pytest.MonkeyPatch):
    row = _mock_row()
    mock_db = _patch_db(monkeypatch, found_row=row)

    await deps_module.get_current_key(authorization="Bearer rr_valid-key")

    assert row.last_used_at is not None
    mock_db.commit.assert_awaited_once()


async def test_lookup_uses_hash_not_plaintext(monkeypatch: pytest.MonkeyPatch):
    """The presented key must never be compared/stored in plaintext."""
    row = _mock_row()
    mock_db = _patch_db(monkeypatch, found_row=row)

    await deps_module.get_current_key(authorization="Bearer rr_valid-key")

    executed_stmt = mock_db.execute.call_args[0][0]
    compiled = str(executed_stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "key_hash" in compiled
