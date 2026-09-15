"""Tests for GET /v1/activity."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.models.enums import (
    ApiKeyRole,
    DeliveryChannel,
    DeliveryStatus,
    FilingDomain,
    RiskLevel,
)


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


def _delivery_row(
    *,
    channel: DeliveryChannel = DeliveryChannel.SLACK,
    status: DeliveryStatus = DeliveryStatus.SENT,
    sent_at=None,
    is_fallback: bool = False,
    error_message: str | None = None,
):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.filing_id = uuid.uuid4()
    row.channel = channel
    row.status = status
    row.is_fallback = is_fallback
    row.sent_at = sent_at
    row.error_message = error_message
    row.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    return row


def _mock_activity_db(monkeypatch: pytest.MonkeyPatch, *, rows: list):
    mock_db = AsyncMock()
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    mock_db.execute = AsyncMock(return_value=result)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)
    return mock_db


def test_activity_visible_to_every_role(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.EXECUTIVE)
    sent_at = datetime(2026, 1, 2, tzinfo=UTC)
    delivery = _delivery_row(sent_at=sent_at)
    _mock_activity_db(
        monkeypatch,
        rows=[(delivery, "Acme Financial Corp", "10-K", FilingDomain.FINANCIAL, RiskLevel.CRITICAL, sent_at)],
    )

    response = TestClient(create_app()).get(
        "/v1/activity", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()[0]
    assert body["entity_name"] == "Acme Financial Corp"
    assert body["channel"] == "slack"
    assert body["status"] == "sent"
    assert body["risk_level"] == "critical"


def test_activity_falls_back_to_created_at_when_never_sent(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST)
    created_at = datetime(2026, 1, 3, tzinfo=UTC)
    delivery = _delivery_row(status=DeliveryStatus.FAILED, sent_at=None)
    delivery.created_at = created_at
    _mock_activity_db(
        monkeypatch,
        rows=[(delivery, "Beta Biopharma Inc", "8-K", FilingDomain.CLINICAL, RiskLevel.HIGH, created_at)],
    )

    response = TestClient(create_app()).get(
        "/v1/activity", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()[0]
    assert body["status"] == "failed"
    assert body["at"].startswith("2026-01-03")


def test_activity_includes_error_message_on_failed_delivery(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST)
    created_at = datetime(2026, 1, 4, tzinfo=UTC)
    delivery = _delivery_row(
        status=DeliveryStatus.FAILED, sent_at=None, error_message="HTTP 500"
    )
    delivery.created_at = created_at
    _mock_activity_db(
        monkeypatch,
        rows=[(delivery, "Gamma Corp", "8-K", FilingDomain.OTHER, RiskLevel.MEDIUM, created_at)],
    )

    response = TestClient(create_app()).get(
        "/v1/activity", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    assert response.json()[0]["error_message"] == "HTTP 500"


def test_activity_error_message_is_null_on_sent_delivery(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST)
    sent_at = datetime(2026, 1, 5, tzinfo=UTC)
    delivery = _delivery_row(sent_at=sent_at)
    _mock_activity_db(
        monkeypatch,
        rows=[(delivery, "Acme Corp", "10-K", FilingDomain.FINANCIAL, RiskLevel.LOW, sent_at)],
    )

    response = TestClient(create_app()).get(
        "/v1/activity", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    assert response.json()[0]["error_message"] is None


def test_activity_rejects_limit_out_of_bounds(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST)
    _mock_activity_db(monkeypatch, rows=[])

    response = TestClient(create_app()).get(
        "/v1/activity?limit=500", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 422
