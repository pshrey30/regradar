"""Tests for POST/GET /v1/invites."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.models.enums import ApiKeyRole


def _authenticated_key_row(role: ApiKeyRole):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.organization_id = uuid.uuid4()
    row.role = role
    row.owner_label = "test-owner"
    row.rate_limit_per_minute = 1000
    row.is_active = True
    row.email = None
    row.password_hash = None
    return row


def _mock_auth_and_rate_limit(monkeypatch: pytest.MonkeyPatch, *, role: ApiKeyRole):
    row = _authenticated_key_row(role)

    mock_auth_db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)
    mock_auth_db.execute = AsyncMock(return_value=result)
    mock_auth_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_auth_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(deps_module, "get_session_factory", lambda: mock_session_factory)

    mock_redis = MagicMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock()
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: mock_redis)


def _mock_route_db(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)

    return mock_db


def _invite_row(*, invite_id: uuid.UUID, used_at=None, used_by_email=None):
    row = MagicMock()
    row.id = invite_id
    row.code_suffix = "abcd"
    row.used_at = used_at
    row.used_by_email = used_by_email
    row.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    return row


def test_create_invite_requires_admin(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST)
    _mock_route_db(monkeypatch)

    response = TestClient(create_app()).post(
        "/v1/invites", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 403


def test_create_invite_returns_plaintext_code_once(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_route_db(monkeypatch)

    response = TestClient(create_app()).post(
        "/v1/invites", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["code"].startswith("rrinv_")
    assert body["code_suffix"] == body["code"][-4:]
    assert body["used_at"] is None
    mock_db.add.assert_called_once()
    created = mock_db.add.call_args.args[0]
    # The stored hash never matches the plaintext code directly.
    assert created.code_hash != body["code"]


def test_list_invites_requires_admin(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ENG_LEAD)
    _mock_route_db(monkeypatch)

    response = TestClient(create_app()).get(
        "/v1/invites", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 403


def test_list_invites_never_includes_plaintext_code(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_route_db(monkeypatch)
    result = MagicMock()
    result.scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=[_invite_row(invite_id=uuid.uuid4())]))
    )
    mock_db.execute = AsyncMock(return_value=result)

    response = TestClient(create_app()).get(
        "/v1/invites", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()[0]
    assert "code" not in body
    assert "code_hash" not in body
    assert body["code_suffix"] == "abcd"


def test_list_invites_shows_used_status(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_route_db(monkeypatch)
    used_row = _invite_row(
        invite_id=uuid.uuid4(),
        used_at=datetime(2026, 1, 2, tzinfo=UTC),
        used_by_email="new-user@example.com",
    )
    result = MagicMock()
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[used_row])))
    mock_db.execute = AsyncMock(return_value=result)

    response = TestClient(create_app()).get(
        "/v1/invites", headers={"Authorization": "Bearer rr_test-key"}
    )

    body = response.json()[0]
    assert body["used_by_email"] == "new-user@example.com"
    assert body["used_at"] is not None
