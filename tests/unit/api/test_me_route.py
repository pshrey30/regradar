"""Tests for GET/PATCH /v1/me."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.models.enums import ApiKeyRole


def _authenticated_key_row(
    role: ApiKeyRole, owner_label: str, organization_id: uuid.UUID, *, key_id: uuid.UUID | None = None
):
    row = MagicMock()
    row.id = key_id or uuid.uuid4()
    row.organization_id = organization_id
    row.role = role
    row.owner_label = owner_label
    row.rate_limit_per_minute = 1000
    row.is_active = True
    row.email = None
    row.password_hash = None
    return row


def _mock_auth_and_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
    *,
    role: ApiKeyRole,
    owner_label: str,
    organization_id: uuid.UUID,
    key_id: uuid.UUID | None = None,
):
    row = _authenticated_key_row(role, owner_label, organization_id, key_id=key_id)

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

    return row


def _mock_route_db(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)

    return mock_db


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
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=role, owner_label="acme-corp-key", organization_id=org_id)

    response = TestClient(create_app()).get(
        "/v1/me", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == role.value
    assert body["display_name"] == "acme-corp-key"
    assert body["organization_id"] == str(org_id)


def test_update_me_changes_display_name(monkeypatch: pytest.MonkeyPatch):
    org_id = uuid.uuid4()
    key_id = uuid.uuid4()
    auth_row = _mock_auth_and_rate_limit(
        monkeypatch,
        role=ApiKeyRole.ANALYST,
        owner_label="old-name",
        organization_id=org_id,
        key_id=key_id,
    )
    mock_db = _mock_route_db(monkeypatch)
    target_row = _authenticated_key_row(ApiKeyRole.ANALYST, "old-name", org_id, key_id=key_id)
    mock_db.get = AsyncMock(return_value=target_row)

    response = TestClient(create_app()).patch(
        "/v1/me",
        json={"display_name": "new-name"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "new-name"
    assert target_row.owner_label == "new-name"
    assert target_row.id == auth_row.id  # only ever updates the caller's own row
    mock_db.commit.assert_awaited()


def test_update_me_rejects_empty_display_name(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(
        monkeypatch, role=ApiKeyRole.ANALYST, owner_label="old-name", organization_id=uuid.uuid4()
    )
    _mock_route_db(monkeypatch)

    response = TestClient(create_app()).patch(
        "/v1/me",
        json={"display_name": ""},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 422
