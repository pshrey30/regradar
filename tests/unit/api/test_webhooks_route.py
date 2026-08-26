"""Tests for POST/GET /v1/webhooks and DELETE /v1/webhooks/{id}."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.api.routers import webhooks as webhooks_module
from regradar.models.enums import ApiKeyRole


def _authenticated_key_row(role: ApiKeyRole, *, key_id: uuid.UUID | None = None):
    row = MagicMock()
    row.id = key_id or uuid.uuid4()
    row.organization_id = uuid.uuid4()
    row.role = role
    row.owner_label = "test-owner"
    row.rate_limit_per_minute = 1000
    row.is_active = True
    return row


def _mock_auth_and_rate_limit(
    monkeypatch: pytest.MonkeyPatch, *, role: ApiKeyRole, key_id: uuid.UUID | None = None
):
    row = _authenticated_key_row(role, key_id=key_id)

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

    return row.id


def _mock_route_db(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Returns the mock db object so tests can further configure execute/
    get/delete for their specific scenario."""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)

    return mock_db


def test_create_webhook_returns_secret_once(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_route_db(monkeypatch)
    monkeypatch.setattr(webhooks_module, "validate_webhook_url", MagicMock())

    response = TestClient(create_app()).post(
        "/v1/webhooks",
        json={"url": "https://example.com/hook"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 201
    body = response.json()
    assert "hmac_secret" in body
    assert len(body["hmac_secret"]) > 20
    assert body["url"] == "https://example.com/hook"
    assert body["is_active"] is True


def test_create_webhook_rejects_private_ip_url(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_route_db(monkeypatch)
    monkeypatch.setattr(
        webhooks_module,
        "validate_webhook_url",
        MagicMock(
            side_effect=webhooks_module.WebhookValidationError("private address rejected")
        ),
    )

    response = TestClient(create_app()).post(
        "/v1/webhooks",
        json={"url": "http://169.254.169.254/latest/meta-data"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_webhook_url"


def _webhook_row(*, webhook_id: uuid.UUID, api_key_id: uuid.UUID, url: str = "https://example.com/hook"):
    row = MagicMock()
    row.id = webhook_id
    row.api_key_id = api_key_id
    row.url = url
    row.hmac_secret = "should-never-appear-in-list-response"
    row.is_active = True
    row.filter_domain = None
    row.filter_min_risk = None
    from datetime import UTC, datetime

    row.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    return row


def test_list_webhooks_never_includes_hmac_secret(monkeypatch: pytest.MonkeyPatch):
    key_id = _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_route_db(monkeypatch)
    result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(
        return_value=[_webhook_row(webhook_id=uuid.uuid4(), api_key_id=key_id)]
    )
    result.scalars = MagicMock(return_value=scalars)
    mock_db.execute = AsyncMock(return_value=result)

    response = TestClient(create_app()).get(
        "/v1/webhooks", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert "hmac_secret" not in body[0]


def test_list_webhooks_scopes_query_to_own_key_for_non_admin(monkeypatch: pytest.MonkeyPatch):
    key_id = _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ENG_LEAD)
    mock_db = _mock_route_db(monkeypatch)
    result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=[])
    result.scalars = MagicMock(return_value=scalars)
    mock_db.execute = AsyncMock(return_value=result)

    TestClient(create_app()).get("/v1/webhooks", headers={"Authorization": "Bearer rr_test-key"})

    executed_stmt = mock_db.execute.call_args[0][0]
    compiled = str(executed_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert str(key_id).replace("-", "") in compiled.replace("-", "")


def test_delete_own_webhook_succeeds(monkeypatch: pytest.MonkeyPatch):
    key_id = _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ENG_LEAD)
    mock_db = _mock_route_db(monkeypatch)
    webhook_id = uuid.uuid4()
    mock_db.get = AsyncMock(return_value=_webhook_row(webhook_id=webhook_id, api_key_id=key_id))
    mock_db.delete = AsyncMock()

    response = TestClient(create_app()).delete(
        f"/v1/webhooks/{webhook_id}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 204
    mock_db.delete.assert_awaited_once()


def test_delete_someone_elses_webhook_returns_404_not_403(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ENG_LEAD)
    mock_db = _mock_route_db(monkeypatch)
    webhook_id = uuid.uuid4()
    other_owner_id = uuid.uuid4()
    mock_db.get = AsyncMock(
        return_value=_webhook_row(webhook_id=webhook_id, api_key_id=other_owner_id)
    )
    mock_db.delete = AsyncMock()

    response = TestClient(create_app()).delete(
        f"/v1/webhooks/{webhook_id}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "webhook_not_found"
    mock_db.delete.assert_not_awaited()


def test_delete_nonexistent_webhook_returns_404(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ENG_LEAD)
    mock_db = _mock_route_db(monkeypatch)
    mock_db.get = AsyncMock(return_value=None)

    response = TestClient(create_app()).delete(
        f"/v1/webhooks/{uuid.uuid4()}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "webhook_not_found"


def test_admin_can_delete_someone_elses_webhook(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_route_db(monkeypatch)
    webhook_id = uuid.uuid4()
    other_owner_id = uuid.uuid4()
    mock_db.get = AsyncMock(
        return_value=_webhook_row(webhook_id=webhook_id, api_key_id=other_owner_id)
    )
    mock_db.delete = AsyncMock()

    response = TestClient(create_app()).delete(
        f"/v1/webhooks/{webhook_id}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 204
    mock_db.delete.assert_awaited_once()
