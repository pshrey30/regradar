"""Tests for GET /v1/me."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.models.enums import ApiKeyRole


def _authenticated_key_row(role: ApiKeyRole, owner_label: str):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.role = role
    row.owner_label = owner_label
    row.rate_limit_per_minute = 1000
    row.is_active = True
    return row


def _mock_auth_and_rate_limit(monkeypatch: pytest.MonkeyPatch, *, role: ApiKeyRole, owner_label: str):
    row = _authenticated_key_row(role, owner_label)

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


def test_me_without_auth_header_returns_401():
    response = TestClient(create_app()).get("/v1/me")

    assert response.status_code == 401


@pytest.mark.parametrize(
    "role",
    [
        ApiKeyRole.ADMIN,
        ApiKeyRole.ANALYST,
        ApiKeyRole.EXECUTIVE,
        ApiKeyRole.LEGAL_COUNSEL,
        ApiKeyRole.ENG_LEAD,
    ],
)
def test_me_returns_role_and_display_name_for_every_role(
    monkeypatch: pytest.MonkeyPatch, role: ApiKeyRole
):
    _mock_auth_and_rate_limit(monkeypatch, role=role, owner_label="acme-corp-key")

    response = TestClient(create_app()).get(
        "/v1/me", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == role.value
    assert body["display_name"] == "acme-corp-key"
    assert body["organization_id"] is None
