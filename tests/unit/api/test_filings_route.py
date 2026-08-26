"""Tests for GET /v1/filings."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.models.enums import ApiKeyRole, FilingDomain, FilingStatus, RiskLevel


def _authenticated_key_row(role: ApiKeyRole):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.organization_id = uuid.uuid4()
    row.role = role
    row.owner_label = "test-owner"
    row.rate_limit_per_minute = 1000
    row.is_active = True
    return row


def _filing_row(
    *,
    entity_name: str = "Acme Corp",
    filing_type: str = "10-K",
    domain: FilingDomain = FilingDomain.FINANCIAL,
    risk_level: RiskLevel = RiskLevel.MEDIUM,
    published_at: datetime | None = None,
    status: FilingStatus = FilingStatus.COMPLETE,
):
    filing = MagicMock()
    filing.id = uuid.uuid4()
    filing.entity_name = entity_name
    filing.filing_type = filing_type
    filing.domain = domain
    filing.risk_level = risk_level
    filing.published_at = published_at or datetime(2026, 1, 1, tzinfo=UTC)
    filing.status = status
    return filing


def _mock_db(monkeypatch: pytest.MonkeyPatch, *, total: int, rows: list[tuple]):
    """rows: list of (filing_mock, executive_brief_str) tuples, as the join query returns."""
    mock_db = AsyncMock()

    count_result = MagicMock()
    count_result.scalar_one = MagicMock(return_value=total)

    page_result = MagicMock()
    page_result.all = MagicMock(return_value=rows)

    # SEC-01's get_authenticated_db issues two set_config() calls on this
    # same session before the route body runs — MagicMock() placeholders for
    # those, then the route's own call order: COUNT query, then the page query.
    mock_db.execute = AsyncMock(
        side_effect=[MagicMock(), MagicMock(), MagicMock(), count_result, page_result]
    )

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)

    return mock_db


def _real_query_calls(mock_db):
    """mock_db.execute's calls after get_authenticated_db's three leading
    set_config() calls — the route's own actual queries, in order."""
    return mock_db.execute.call_args_list[3:]


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


def test_list_filings_returns_paginated_response(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    filing = _filing_row()
    _mock_db(monkeypatch, total=1, rows=[(filing, "This is the executive brief.")])

    response = TestClient(create_app()).get(
        "/v1/filings", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert len(body["data"]) == 1
    item = body["data"][0]
    assert item["entity_name"] == "Acme Corp"
    assert item["filing_type"] == "10-K"
    assert item["domain"] == "financial"
    assert item["risk_level"] == "medium"
    assert item["executive_brief"] == "This is the executive brief."


def test_list_filings_response_never_includes_extraction_fields(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    filing = _filing_row()
    _mock_db(monkeypatch, total=1, rows=[(filing, "brief text")])

    response = TestClient(create_app()).get(
        "/v1/filings", headers={"Authorization": "Bearer rr_test-key"}
    )

    item_keys = set(response.json()["data"][0].keys())
    extraction_shaped_keys = {"obligations", "deadlines", "risk_flags", "affected_products",
                               "key_entities", "competitor_mentions"}
    assert item_keys.isdisjoint(extraction_shaped_keys)
    assert item_keys == {
        "id", "entity_name", "filing_type", "domain", "risk_level", "published_at",
        "executive_brief",
    }


def test_list_filings_without_auth_header_returns_401(monkeypatch: pytest.MonkeyPatch):
    response = TestClient(create_app()).get("/v1/filings")

    assert response.status_code == 401


def test_list_filings_domain_filter_applies_to_query(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_db(monkeypatch, total=0, rows=[])

    TestClient(create_app()).get(
        "/v1/filings",
        params={"domain": "clinical"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    # Both the COUNT and the page SELECT must carry the domain filter. Assert the
    # literal filter *value* ('clinical') appears in the compiled WHERE clause —
    # not just the column name "domain", which is guaranteed present in the page
    # statement regardless (it's a selected column) and would pass even if the
    # WHERE-clause filter were accidentally dropped from the page query.
    for call in _real_query_calls(mock_db):
        compiled = str(call.args[0].compile(compile_kwargs={"literal_binds": True}))
        assert "'clinical'" in compiled


def test_list_filings_invalid_domain_returns_422(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_db(monkeypatch, total=0, rows=[])

    response = TestClient(create_app()).get(
        "/v1/filings",
        params={"domain": "not-a-real-domain"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_executive_role_without_risk_param_only_sees_high_critical(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.EXECUTIVE)
    mock_db = _mock_db(monkeypatch, total=0, rows=[])

    TestClient(create_app()).get(
        "/v1/filings", headers={"Authorization": "Bearer rr_test-key"}
    )

    count_stmt = _real_query_calls(mock_db)[0].args[0]
    compiled = str(count_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "'high'" in compiled or "high" in compiled.lower()
    assert "'critical'" in compiled or "critical" in compiled.lower()
    assert "'low'" not in compiled.lower()
    assert "'medium'" not in compiled.lower()


def test_executive_role_requesting_low_risk_gets_empty_result_not_error(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.EXECUTIVE)
    _mock_db(monkeypatch, total=0, rows=[])

    response = TestClient(create_app()).get(
        "/v1/filings",
        params={"risk": "low"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == []
    assert response.json()["total"] == 0


def test_non_executive_role_is_unaffected_by_executive_risk_restriction(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST)
    mock_db = _mock_db(monkeypatch, total=0, rows=[])

    TestClient(create_app()).get(
        "/v1/filings",
        params={"risk": "low"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    count_stmt = _real_query_calls(mock_db)[0].args[0]
    compiled = str(count_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "low" in compiled.lower()


def test_page_size_over_max_returns_422(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_db(monkeypatch, total=0, rows=[])

    response = TestClient(create_app()).get(
        "/v1/filings",
        params={"page_size": 101},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 422


def test_list_filings_since_filter_applies_to_query(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_db(monkeypatch, total=0, rows=[])

    TestClient(create_app()).get(
        "/v1/filings",
        params={"since": "2026-01-01T00:00:00Z"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    # Assert the literal filter *value* appears in the compiled WHERE clause —
    # not just the string "published_at", which is guaranteed present in the
    # page statement regardless (it's the ORDER BY column) and would pass even
    # if the WHERE-clause filter were accidentally dropped from the page query.
    for call in _real_query_calls(mock_db):
        compiled = str(call.args[0].compile(compile_kwargs={"literal_binds": True}))
        assert "2026-01-01 00:00:00" in compiled


def test_list_filings_page_query_orders_by_published_at_then_id(
    monkeypatch: pytest.MonkeyPatch,
):
    """Pagination must be deterministic: ties on published_at (common in practice,
    since regulatory feeds publish in batches) need a secondary sort key or
    consecutive pages can return the same filing twice and silently skip another.
    """
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_db(monkeypatch, total=0, rows=[])

    TestClient(create_app()).get(
        "/v1/filings", headers={"Authorization": "Bearer rr_test-key"}
    )

    page_stmt = _real_query_calls(mock_db)[1].args[0]
    compiled = str(page_stmt.compile(compile_kwargs={"literal_binds": True}))
    order_by_clause = compiled.split("ORDER BY", 1)[1]
    assert "published_at" in order_by_clause
    assert "id" in order_by_clause
    assert len(page_stmt._order_by_clauses) == 2


def test_page_over_max_returns_422(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_db(monkeypatch, total=0, rows=[])

    response = TestClient(create_app()).get(
        "/v1/filings",
        params={"page": 100_001},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 422


def test_naive_since_is_normalized_to_utc_not_server_local_time(
    monkeypatch: pytest.MonkeyPatch,
):
    """A naive `since` (no timezone offset in the query string) must be treated
    as UTC, not silently reinterpreted in the server's local timezone by
    asyncpg's encoder (which calls naive_datetime.astimezone(utc)).
    """
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_db(monkeypatch, total=0, rows=[])

    TestClient(create_app()).get(
        "/v1/filings",
        params={"since": "2026-01-01T00:00:00"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    for call in _real_query_calls(mock_db):
        compiled = str(call.args[0].compile(compile_kwargs={"literal_binds": True}))
        assert "2026-01-01 00:00:00+00" in compiled


def test_list_filings_query_only_selects_complete_status_joined_to_briefs(
    monkeypatch: pytest.MonkeyPatch,
):
    """Structural proof that the query mechanically excludes incomplete filings:
    status=complete is always in the WHERE clause, and the page query is an
    inner join to briefs (a filing with no Brief row can never match an inner
    join, regardless of any other filter) — this is what actually keeps a
    still-processing filing (no Brief yet) out of every possible result,
    verified end-to-end with real data in Task 3's live verification.
    """
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_db(monkeypatch, total=0, rows=[])

    TestClient(create_app()).get(
        "/v1/filings", headers={"Authorization": "Bearer rr_test-key"}
    )

    count_stmt, page_stmt = (call.args[0] for call in _real_query_calls(mock_db))
    count_compiled = str(count_stmt.compile(compile_kwargs={"literal_binds": True}))
    page_compiled = str(page_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "complete" in count_compiled.lower()
    assert "complete" in page_compiled.lower()
    assert "join briefs" in count_compiled.lower() or "join public.briefs" in count_compiled.lower()
    assert "join briefs" in page_compiled.lower() or "join public.briefs" in page_compiled.lower()
