"""Tests for GET /v1/filings/{id}/pdf-url."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.api.routers import filings as filings_module
from regradar.models.enums import ApiKeyRole


def _authenticated_key_row(role: ApiKeyRole = ApiKeyRole.ADMIN):
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


def _mock_auth_and_rate_limit(monkeypatch: pytest.MonkeyPatch, *, role: ApiKeyRole = ApiKeyRole.ADMIN):
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


def _mock_filing_lookup(monkeypatch: pytest.MonkeyPatch, *, filing):
    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.execute = AsyncMock(side_effect=[MagicMock(), MagicMock(), MagicMock()])

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)

    return mock_db


def test_pdf_url_returns_404_for_unknown_filing(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch)
    _mock_filing_lookup(monkeypatch, filing=None)

    response = TestClient(create_app()).get(
        f"/v1/filings/{uuid.uuid4()}/pdf-url", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "pdf_not_found"


def test_pdf_url_returns_404_when_filing_has_no_stored_pdf(monkeypatch: pytest.MonkeyPatch):
    filing = MagicMock()
    filing.raw_pdf_s3_key = None
    _mock_auth_and_rate_limit(monkeypatch)
    _mock_filing_lookup(monkeypatch, filing=filing)

    response = TestClient(create_app()).get(
        f"/v1/filings/{uuid.uuid4()}/pdf-url", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "pdf_not_found"


def test_pdf_url_returns_signed_url_when_pdf_exists(monkeypatch: pytest.MonkeyPatch):
    filing = MagicMock()
    filing.raw_pdf_s3_key = "abcd1234/filing.pdf"
    _mock_auth_and_rate_limit(monkeypatch)
    _mock_filing_lookup(monkeypatch, filing=filing)

    monkeypatch.setattr(
        filings_module,
        "generate_presigned_pdf_url",
        lambda key, **kwargs: f"https://s3.example.com/{key}?signed=1",
    )

    response = TestClient(create_app()).get(
        f"/v1/filings/{uuid.uuid4()}/pdf-url", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    assert response.json() == {"url": "https://s3.example.com/abcd1234/filing.pdf?signed=1"}


def test_pdf_url_without_auth_header_returns_401():
    response = TestClient(create_app()).get(f"/v1/filings/{uuid.uuid4()}/pdf-url")

    assert response.status_code == 401
