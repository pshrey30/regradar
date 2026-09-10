"""Tests for POST /v1/auth/change-password."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.core.passwords import hash_password
from regradar.models.enums import ApiKeyRole


def _authenticated_key_row(*, key_id: uuid.UUID):
    row = MagicMock()
    row.id = key_id
    row.organization_id = uuid.uuid4()
    row.role = ApiKeyRole.ANALYST
    row.owner_label = "test-owner"
    row.rate_limit_per_minute = 1000
    row.is_active = True
    row.email = None
    row.password_hash = None
    return row


def _mock_auth_and_rate_limit(monkeypatch: pytest.MonkeyPatch, *, key_id: uuid.UUID):
    row = _authenticated_key_row(key_id=key_id)

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
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)

    return mock_db


def test_change_password_succeeds_with_correct_current_password(monkeypatch: pytest.MonkeyPatch):
    key_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, key_id=key_id)
    mock_db = _mock_route_db(monkeypatch)
    target = MagicMock()
    target.password_hash = hash_password("OldPassword123")
    mock_db.get = AsyncMock(return_value=target)

    response = TestClient(create_app()).post(
        "/v1/auth/change-password",
        json={"current_password": "OldPassword123", "new_password": "NewPassword456"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 204
    assert target.password_hash != hash_password("OldPassword123")  # rehashed to a new value
    mock_db.commit.assert_awaited()


def test_change_password_rejects_wrong_current_password(monkeypatch: pytest.MonkeyPatch):
    key_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, key_id=key_id)
    mock_db = _mock_route_db(monkeypatch)
    target = MagicMock()
    target.password_hash = hash_password("OldPassword123")
    mock_db.get = AsyncMock(return_value=target)

    response = TestClient(create_app()).post(
        "/v1/auth/change-password",
        json={"current_password": "WrongPassword", "new_password": "NewPassword456"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "incorrect_password"


def test_change_password_rejects_weak_new_password(monkeypatch: pytest.MonkeyPatch):
    key_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, key_id=key_id)
    mock_db = _mock_route_db(monkeypatch)
    target = MagicMock()
    target.password_hash = hash_password("OldPassword123")
    mock_db.get = AsyncMock(return_value=target)

    response = TestClient(create_app()).post(
        "/v1/auth/change-password",
        json={"current_password": "OldPassword123", "new_password": "short"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "weak_password"


def test_change_password_rejects_sso_only_account(monkeypatch: pytest.MonkeyPatch):
    key_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, key_id=key_id)
    mock_db = _mock_route_db(monkeypatch)
    target = MagicMock()
    target.password_hash = None  # Google-SSO-only account
    mock_db.get = AsyncMock(return_value=target)

    response = TestClient(create_app()).post(
        "/v1/auth/change-password",
        json={"current_password": "anything", "new_password": "NewPassword456"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "no_password_set"
