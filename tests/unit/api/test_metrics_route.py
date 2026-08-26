"""Tests for GET /v1/metrics."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.models.enums import ApiKeyRole, EvalRunType


def _authenticated_key_row(role: ApiKeyRole):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.role = role
    row.owner_label = "test-owner"
    row.rate_limit_per_minute = 1000
    row.is_active = True
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


def _eval_run_row(*, ragas_faithfulness: float | None = 0.91, created_at: datetime | None = None):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.run_type = EvalRunType.MANUAL
    row.prompt_version = "v1"
    row.git_commit_sha = "abc123"
    row.passed = True
    row.created_at = created_at or datetime(2026, 1, 1, tzinfo=UTC)
    row.ragas_faithfulness = ragas_faithfulness
    row.ragas_context_recall = 0.85
    row.rouge_l = 0.5
    row.alert_precision = 0.96
    row.alert_recall = 0.98
    row.hallucination_rate = 0.01
    row.extraction_f1 = 0.88
    row.p99_latency_ms = 120_000
    row.avg_cost_per_filing_usd = 0.12
    return row


def _mock_eval_runs_db_single(monkeypatch: pytest.MonkeyPatch, *, row):
    mock_db = AsyncMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.first = MagicMock(return_value=row)
    result.scalars = MagicMock(return_value=scalars)
    mock_db.execute = AsyncMock(return_value=result)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)
    return mock_db


def _mock_eval_runs_db_list(monkeypatch: pytest.MonkeyPatch, *, rows: list):
    mock_db = AsyncMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=rows)
    result.scalars = MagicMock(return_value=scalars)
    mock_db.execute = AsyncMock(return_value=result)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)
    return mock_db


@pytest.mark.parametrize(
    "role", [ApiKeyRole.ANALYST, ApiKeyRole.EXECUTIVE, ApiKeyRole.LEGAL_COUNSEL]
)
def test_metrics_returns_403_for_non_admin_eng_lead_roles(
    monkeypatch: pytest.MonkeyPatch, role: ApiKeyRole
):
    _mock_auth_and_rate_limit(monkeypatch, role=role)
    _mock_eval_runs_db_single(monkeypatch, row=None)

    response = TestClient(create_app()).get(
        "/v1/metrics", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_metrics_without_auth_header_returns_401():
    response = TestClient(create_app()).get("/v1/metrics")

    assert response.status_code == 401


def test_metrics_returns_404_when_no_eval_data_exists(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_eval_runs_db_single(monkeypatch, row=None)

    response = TestClient(create_app()).get(
        "/v1/metrics", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_eval_data"


def test_metrics_returns_latest_row_with_targets(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_eval_runs_db_single(monkeypatch, row=_eval_run_row())

    response = TestClient(create_app()).get(
        "/v1/metrics", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ragas_faithfulness"] == {"value": 0.91, "target": 0.87}
    assert body["hallucination_rate"] == {"value": 0.01, "target": None}
    assert body["passed"] is True


def test_metrics_with_date_range_returns_list(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ENG_LEAD)
    rows = [_eval_run_row(), _eval_run_row(ragas_faithfulness=0.5)]
    _mock_eval_runs_db_list(monkeypatch, rows=rows)

    response = TestClient(create_app()).get(
        "/v1/metrics",
        params={"since": "2026-01-01T00:00:00Z", "until": "2026-02-01T00:00:00Z"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 2


def test_metrics_with_date_range_returns_empty_list_not_error(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_eval_runs_db_list(monkeypatch, rows=[])

    response = TestClient(create_app()).get(
        "/v1/metrics",
        params={"since": "2026-01-01T00:00:00Z"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    assert response.json() == []
