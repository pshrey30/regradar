"""Tests for POST /v1/filings/{id}/alert (Admin-only manual alert send)."""

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
os.environ.setdefault("GROQ_API_KEY", "test")
os.environ.setdefault("SEC_EDGAR_USER_AGENT", "RegRadar/1.0 (test@example.com)")

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.delivery.types import DeliveryResult
from regradar.models.enums import ApiKeyRole, DeliveryStatus, RiskLevel


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


def _mock_authenticated_db(monkeypatch: pytest.MonkeyPatch, *, mock_db):
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)


def _filing_row(filing_id):
    filing = MagicMock()
    filing.id = filing_id
    filing.entity_name = "Meridian Biotech Inc"
    filing.filing_type = "8-K"
    filing.risk_level = RiskLevel.HIGH
    return filing


def _brief_row(filing_id):
    brief = MagicMock()
    brief.filing_id = filing_id
    brief.executive_brief = "A concise 3-5 line executive summary of the filing."
    return brief


def test_send_manual_alert_rejects_non_admin(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST)
    mock_db = AsyncMock()
    _mock_authenticated_db(monkeypatch, mock_db=mock_db)

    response = TestClient(create_app()).post(
        f"/v1/filings/{uuid.uuid4()}/alert",
        json={"email": "someone@example.com"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 403


def test_send_manual_alert_returns_404_when_filing_not_found(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=None)
    _mock_authenticated_db(monkeypatch, mock_db=mock_db)

    response = TestClient(create_app()).post(
        f"/v1/filings/{uuid.uuid4()}/alert",
        json={"email": "someone@example.com"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 404


def test_send_manual_alert_rejects_invalid_email(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=_filing_row(uuid.uuid4()))
    _mock_authenticated_db(monkeypatch, mock_db=mock_db)

    response = TestClient(create_app()).post(
        f"/v1/filings/{uuid.uuid4()}/alert",
        json={"email": "not-an-email"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 422


def test_send_manual_alert_returns_409_when_filing_not_summarized(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    filing_id = uuid.uuid4()
    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=_filing_row(filing_id))
    no_brief_result = MagicMock()
    no_brief_result.scalar_one_or_none = MagicMock(return_value=None)
    mock_db.execute = AsyncMock(return_value=no_brief_result)
    _mock_authenticated_db(monkeypatch, mock_db=mock_db)

    response = TestClient(create_app()).post(
        f"/v1/filings/{filing_id}/alert",
        json={"email": "someone@example.com"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "filing_not_summarized"


def test_send_manual_alert_sends_and_records_delivery_on_success(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    filing_id = uuid.uuid4()
    filing = _filing_row(filing_id)
    brief = _brief_row(filing_id)

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    brief_result = MagicMock()
    brief_result.scalar_one_or_none = MagicMock(return_value=brief)
    mock_db.execute = AsyncMock(return_value=brief_result)
    _mock_authenticated_db(monkeypatch, mock_db=mock_db)

    import regradar.agents.delivery_agent as delivery_agent_module
    import regradar.delivery.sendgrid_client as sendgrid_client_module

    mock_send_email = AsyncMock(
        return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=202)
    )
    monkeypatch.setattr(sendgrid_client_module, "send_email_alert", mock_send_email)

    mock_record_delivery = AsyncMock()
    monkeypatch.setattr(delivery_agent_module, "_record_delivery", mock_record_delivery)

    response = TestClient(create_app()).post(
        f"/v1/filings/{filing_id}/alert",
        json={"email": "compliance@meridianbiotech.test"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "sent"
    assert body["recipient"] == "compliance@meridianbiotech.test"
    mock_send_email.assert_awaited_once()
    _, kwargs = mock_send_email.call_args
    assert kwargs["recipient"] == "compliance@meridianbiotech.test"
    assert kwargs["executive_brief"] == brief.executive_brief
    mock_record_delivery.assert_awaited_once()


def test_send_manual_alert_returns_502_when_send_fails(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    filing_id = uuid.uuid4()
    filing = _filing_row(filing_id)
    brief = _brief_row(filing_id)

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    brief_result = MagicMock()
    brief_result.scalar_one_or_none = MagicMock(return_value=brief)
    mock_db.execute = AsyncMock(return_value=brief_result)
    _mock_authenticated_db(monkeypatch, mock_db=mock_db)

    import regradar.agents.delivery_agent as delivery_agent_module
    import regradar.delivery.sendgrid_client as sendgrid_client_module

    mock_send_email = AsyncMock(
        return_value=DeliveryResult(
            status=DeliveryStatus.FAILED, response_code=500, error_message="HTTP 500"
        )
    )
    monkeypatch.setattr(sendgrid_client_module, "send_email_alert", mock_send_email)
    monkeypatch.setattr(delivery_agent_module, "_record_delivery", AsyncMock())

    response = TestClient(create_app()).post(
        f"/v1/filings/{filing_id}/alert",
        json={"email": "someone@example.com"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "alert_send_failed"
