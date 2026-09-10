"""Tests for POST/GET /v1/api-keys and DELETE /v1/api-keys/{id}."""

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


def _authenticated_key_row(role: ApiKeyRole, *, org_id: uuid.UUID | None = None):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.organization_id = org_id or uuid.uuid4()
    row.role = role
    row.owner_label = "test-owner"
    row.rate_limit_per_minute = 1000
    row.is_active = True
    return row


def _mock_auth_and_rate_limit(
    monkeypatch: pytest.MonkeyPatch, *, role: ApiKeyRole, org_id: uuid.UUID | None = None
):
    row = _authenticated_key_row(role, org_id=org_id)

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

    return row.organization_id


def _mock_route_db(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)

    return mock_db


def test_create_api_key_requires_admin(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST)
    _mock_route_db(monkeypatch)

    response = TestClient(create_app()).post(
        "/v1/api-keys",
        json={"owner_label": "CI bot", "role": "analyst"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_create_api_key_returns_plaintext_key_once(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_route_db(monkeypatch)

    response = TestClient(create_app()).post(
        "/v1/api-keys",
        json={"owner_label": "CI bot", "role": "analyst"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["key"].startswith("rr_")
    assert body["key_suffix"] == body["key"][-4:]
    assert body["owner_label"] == "CI bot"
    assert body["role"] == "analyst"
    assert body["is_active"] is True


def test_create_api_key_defaults_rate_limit(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_route_db(monkeypatch)

    response = TestClient(create_app()).post(
        "/v1/api-keys",
        json={"owner_label": "CI bot", "role": "analyst"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.json()["rate_limit_per_minute"] == 60


def _api_key_row(
    *,
    key_id: uuid.UUID,
    org_id: uuid.UUID,
    role: ApiKeyRole = ApiKeyRole.ANALYST,
    is_active: bool = True,
):
    row = MagicMock()
    row.id = key_id
    row.organization_id = org_id
    row.owner_label = "some key"
    row.role = role
    row.is_active = is_active
    row.rate_limit_per_minute = 60
    row.key_suffix = "abcd"
    row.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    row.last_used_at = None
    return row


def test_list_api_keys_requires_admin(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ENG_LEAD)
    _mock_route_db(monkeypatch)

    response = TestClient(create_app()).get(
        "/v1/api-keys", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 403


def test_list_api_keys_never_includes_plaintext_or_hash(monkeypatch: pytest.MonkeyPatch):
    org_id = _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_route_db(monkeypatch)
    result = MagicMock()
    result.scalars = MagicMock(
        return_value=MagicMock(
            all=MagicMock(return_value=[_api_key_row(key_id=uuid.uuid4(), org_id=org_id)])
        )
    )
    mock_db.execute = AsyncMock(return_value=result)

    response = TestClient(create_app()).get(
        "/v1/api-keys", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()[0]
    assert "key" not in body
    assert "key_hash" not in body
    assert body["key_suffix"] == "abcd"


def test_list_api_keys_scopes_to_own_organization(monkeypatch: pytest.MonkeyPatch):
    org_id = _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_route_db(monkeypatch)
    mock_db.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
    )

    TestClient(create_app()).get("/v1/api-keys", headers={"Authorization": "Bearer rr_test-key"})

    executed_stmt = mock_db.execute.call_args[0][0]
    compiled = str(executed_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert str(org_id).replace("-", "") in compiled.replace("-", "")


def test_revoke_api_key_requires_admin(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST)
    _mock_route_db(monkeypatch)

    response = TestClient(create_app()).delete(
        f"/v1/api-keys/{uuid.uuid4()}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 403


def test_revoke_api_key_sets_inactive(monkeypatch: pytest.MonkeyPatch):
    org_id = _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_route_db(monkeypatch)
    key_id = uuid.uuid4()
    target = _api_key_row(key_id=key_id, org_id=org_id, is_active=True)
    mock_db.get = AsyncMock(return_value=target)

    response = TestClient(create_app()).delete(
        f"/v1/api-keys/{key_id}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert target.is_active is False


def test_revoke_nonexistent_api_key_returns_404(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_route_db(monkeypatch)
    mock_db.get = AsyncMock(return_value=None)

    response = TestClient(create_app()).delete(
        f"/v1/api-keys/{uuid.uuid4()}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "api_key_not_found"


def test_revoke_api_key_from_another_organization_returns_404(monkeypatch: pytest.MonkeyPatch):
    org_id = _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_route_db(monkeypatch)
    key_id = uuid.uuid4()
    other_org_id = uuid.uuid4()
    assert other_org_id != org_id
    mock_db.get = AsyncMock(return_value=_api_key_row(key_id=key_id, org_id=other_org_id))

    response = TestClient(create_app()).delete(
        f"/v1/api-keys/{key_id}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 404
