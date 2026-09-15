"""Tests for GET /v1/filings/pending and POST /v1/filings/{id}/process.

Both admin-only endpoints. process_pending_filing lazily imports
regradar.workers.pipeline_tasks, which imports workers.celery_app —
celery_app.py resolves Settings eagerly at import time (see its own
module docstring), so required env vars must be set *before* that import
happens, i.e. before this module's own import-time code runs, not via a
pytest fixture. Mirrors tests/unit/workers/test_pipeline_tasks.py's own
defensive setup for the same reason.
"""

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
os.environ.setdefault("SEC_EDGAR_USER_AGENT", "RegRadar/1.0 (test@example.com)")

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.models.enums import ApiKeyRole, FilingSource, FilingStatus


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


def _pending_filing_row():
    filing = MagicMock()
    filing.id = uuid.uuid4()
    filing.entity_name = "Stuck Corp"
    filing.filing_type = "10-K"
    filing.source = FilingSource.SEC
    filing.status = FilingStatus.INGESTED
    filing.ingested_at = datetime(2026, 1, 1, tzinfo=UTC)
    filing.processing_error = None
    return filing


def _mock_authenticated_db(monkeypatch: pytest.MonkeyPatch, *, mock_db):
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)


def test_list_pending_filings_rejects_non_admin(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST)
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock())
    _mock_authenticated_db(monkeypatch, mock_db=mock_db)

    response = TestClient(create_app()).get(
        "/v1/filings/pending", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 403


def test_list_pending_filings_returns_non_complete_filings_for_admin(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    filing = _pending_filing_row()

    mock_db = AsyncMock()
    query_result = MagicMock()
    query_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[filing])))
    # get_authenticated_db issues three set_config() calls before the route
    # body's own query runs.
    mock_db.execute = AsyncMock(side_effect=[MagicMock(), MagicMock(), MagicMock(), query_result])
    _mock_authenticated_db(monkeypatch, mock_db=mock_db)

    response = TestClient(create_app()).get(
        "/v1/filings/pending", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()["data"][0]
    assert body["entity_name"] == "Stuck Corp"
    assert body["status"] == "ingested"
    assert body["source"] == "SEC"


def test_process_filing_rejects_non_admin(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST)
    mock_db = AsyncMock()
    _mock_authenticated_db(monkeypatch, mock_db=mock_db)

    response = TestClient(create_app()).post(
        f"/v1/filings/{uuid.uuid4()}/process", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 403


def test_process_filing_returns_404_when_not_found(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=None)
    _mock_authenticated_db(monkeypatch, mock_db=mock_db)

    response = TestClient(create_app()).post(
        f"/v1/filings/{uuid.uuid4()}/process", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 404


def test_process_filing_runs_pipeline_and_returns_refreshed_status(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.status = FilingStatus.COMPLETE

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.refresh = AsyncMock()
    _mock_authenticated_db(monkeypatch, mock_db=mock_db)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    mock_run_pipeline = AsyncMock()
    monkeypatch.setattr(pipeline_tasks_module, "_run_pipeline_for_filing", mock_run_pipeline)

    response = TestClient(create_app()).post(
        f"/v1/filings/{filing_id}/process", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "complete"
    mock_run_pipeline.assert_awaited_once_with(str(filing_id))
    mock_db.refresh.assert_awaited_once_with(filing)


def test_process_filing_marks_failed_when_pipeline_raises(monkeypatch: pytest.MonkeyPatch):
    """A pipeline failure must not 500 the request — it's reported back as
    the filing's (now failed) status, matching process-pending's own
    per-filing-failure-doesn't-crash-the-batch philosophy."""
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.status = FilingStatus.FAILED

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.refresh = AsyncMock()
    _mock_authenticated_db(monkeypatch, mock_db=mock_db)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module,
        "_run_pipeline_for_filing",
        AsyncMock(side_effect=RuntimeError("LLM call failed")),
    )
    mock_mark_failed = AsyncMock()
    monkeypatch.setattr(pipeline_tasks_module, "_mark_filing_failed", mock_mark_failed)

    response = TestClient(create_app()).post(
        f"/v1/filings/{filing_id}/process", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    mock_mark_failed.assert_awaited_once_with(str(filing_id), "LLM call failed")
