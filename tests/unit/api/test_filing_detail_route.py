"""Tests for GET /v1/filings/{id}."""

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
    row.role = role
    row.owner_label = "test-owner"
    row.rate_limit_per_minute = 1000
    row.is_active = True
    return row


def _filing_row(
    filing_id: uuid.UUID | None = None,
    *,
    entity_name: str = "Acme Corp",
    filing_type: str = "10-K",
    domain: FilingDomain = FilingDomain.FINANCIAL,
    risk_level: RiskLevel = RiskLevel.MEDIUM,
    priority_score: float = 0.5,
    published_at: datetime | None = None,
    status: FilingStatus = FilingStatus.COMPLETE,
):
    filing = MagicMock()
    filing.id = filing_id or uuid.uuid4()
    filing.entity_name = entity_name
    filing.filing_type = filing_type
    filing.domain = domain
    filing.risk_level = risk_level
    filing.priority_score = priority_score
    filing.published_at = published_at or datetime(2026, 1, 1, tzinfo=UTC)
    filing.status = status
    return filing


def _brief_row(*, executive_brief: str = "The executive brief text."):
    brief = MagicMock()
    brief.executive_brief = executive_brief
    return brief


def _extraction_row(*, similar_filing_ids: list[str] | None = None):
    extraction = MagicMock()
    extraction.obligations = [{"description": "File a report"}]
    extraction.deadlines = [{"description": "By Q2", "date": "2026-06-30"}]
    extraction.risk_flags = ["late filing"]
    extraction.affected_products = ["Widget X"]
    extraction.key_entities = ["Acme Corp"]
    extraction.competitor_mentions = ["Globex"]
    extraction.similar_filing_ids = similar_filing_ids
    return extraction


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


def _mock_detail_db(
    monkeypatch: pytest.MonkeyPatch,
    *,
    filing,
    brief=None,
    extraction=None,
    similar_filings: list | None = None,
):
    """filing=None simulates a nonexistent filing (db.get returns None).

    execute() call order matches the route's own: brief lookup, then
    extraction lookup, then (only if extraction.similar_filing_ids is
    truthy) the similar-filings lookup.
    """
    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)

    brief_result = MagicMock()
    brief_result.scalar_one_or_none = MagicMock(return_value=brief)

    extraction_result = MagicMock()
    extraction_result.scalar_one_or_none = MagicMock(return_value=extraction)

    execute_results = [brief_result, extraction_result]
    if extraction is not None and extraction.similar_filing_ids:
        similar_result = MagicMock()
        similar_scalars = MagicMock()
        similar_scalars.all = MagicMock(return_value=similar_filings or [])
        similar_result.scalars = MagicMock(return_value=similar_scalars)
        execute_results.append(similar_result)

    mock_db.execute = AsyncMock(side_effect=execute_results)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)

    return mock_db


def test_get_filing_returns_404_for_unknown_id(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_detail_db(monkeypatch, filing=None)

    response = TestClient(create_app()).get(
        f"/v1/filings/{uuid.uuid4()}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "filing_not_found"


def test_get_filing_without_auth_header_returns_401():
    response = TestClient(create_app()).get(f"/v1/filings/{uuid.uuid4()}")

    assert response.status_code == 401


def test_get_filing_admin_role_includes_extraction(monkeypatch: pytest.MonkeyPatch):
    filing = _filing_row()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_detail_db(
        monkeypatch, filing=filing, brief=_brief_row(), extraction=_extraction_row()
    )

    response = TestClient(create_app()).get(
        f"/v1/filings/{filing.id}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()
    assert "extraction" in body
    assert body["extraction"]["obligations"] == [{"description": "File a report"}]
    assert body["extraction"]["key_entities"] == ["Acme Corp"]


@pytest.mark.parametrize(
    "role",
    [ApiKeyRole.ADMIN, ApiKeyRole.ANALYST, ApiKeyRole.LEGAL_COUNSEL, ApiKeyRole.ENG_LEAD],
)
def test_get_filing_all_permitted_roles_include_extraction(
    monkeypatch: pytest.MonkeyPatch, role: ApiKeyRole
):
    filing = _filing_row()
    _mock_auth_and_rate_limit(monkeypatch, role=role)
    _mock_detail_db(
        monkeypatch, filing=filing, brief=_brief_row(), extraction=_extraction_row()
    )

    response = TestClient(create_app()).get(
        f"/v1/filings/{filing.id}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert "extraction" in response.json()


def test_get_filing_executive_role_omits_extraction_key_entirely(
    monkeypatch: pytest.MonkeyPatch,
):
    filing = _filing_row()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.EXECUTIVE)
    _mock_detail_db(
        monkeypatch, filing=filing, brief=_brief_row(), extraction=_extraction_row()
    )

    response = TestClient(create_app()).get(
        f"/v1/filings/{filing.id}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()
    assert "extraction" not in body
    # Brief-level data is still present for Executive.
    assert body["brief"]["executive_brief"] == "The executive brief text."


def test_get_filing_executive_role_omits_extraction_even_when_none_exists(
    monkeypatch: pytest.MonkeyPatch,
):
    """Confirms the omission is unconditional for Executive, not merely a
    side effect of there being nothing to show.
    """
    filing = _filing_row()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.EXECUTIVE)
    _mock_detail_db(monkeypatch, filing=filing, brief=_brief_row(), extraction=None)

    response = TestClient(create_app()).get(
        f"/v1/filings/{filing.id}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert "extraction" not in response.json()


def test_get_filing_non_executive_role_gets_null_extraction_when_none_exists(
    monkeypatch: pytest.MonkeyPatch,
):
    """A permitted role still sees the key, just null, when analysis hasn't
    run yet — distinguishing "not permitted" (key absent) from "no data
    yet" (key present, null).
    """
    filing = _filing_row(status=FilingStatus.ANALYZING)
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_detail_db(monkeypatch, filing=filing, brief=None, extraction=None)

    response = TestClient(create_app()).get(
        f"/v1/filings/{filing.id}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()
    assert "extraction" in body
    assert body["extraction"] is None
    assert body["brief"] is None


def test_get_filing_includes_similar_filings_resolved_from_extraction(
    monkeypatch: pytest.MonkeyPatch,
):
    filing = _filing_row()
    similar = _filing_row(entity_name="Similar Co", filing_type="8-K")
    extraction = _extraction_row(similar_filing_ids=[str(similar.id)])
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_detail_db(
        monkeypatch,
        filing=filing,
        brief=_brief_row(),
        extraction=extraction,
        similar_filings=[similar],
    )

    response = TestClient(create_app()).get(
        f"/v1/filings/{filing.id}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    similar_filings = response.json()["similar_filings"]
    assert len(similar_filings) == 1
    assert similar_filings[0]["entity_name"] == "Similar Co"
    assert similar_filings[0]["filing_type"] == "8-K"


def test_get_filing_similar_filings_empty_when_extraction_has_no_similar_ids(
    monkeypatch: pytest.MonkeyPatch,
):
    filing = _filing_row()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_detail_db(
        monkeypatch,
        filing=filing,
        brief=_brief_row(),
        extraction=_extraction_row(similar_filing_ids=None),
    )

    response = TestClient(create_app()).get(
        f"/v1/filings/{filing.id}", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.json()["similar_filings"] == []


def test_get_filing_includes_risk_score_and_status(monkeypatch: pytest.MonkeyPatch):
    filing = _filing_row(risk_level=RiskLevel.CRITICAL, priority_score=0.92)
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_detail_db(monkeypatch, filing=filing, brief=_brief_row(), extraction=None)

    response = TestClient(create_app()).get(
        f"/v1/filings/{filing.id}", headers={"Authorization": "Bearer rr_test-key"}
    )

    body = response.json()
    assert body["risk_level"] == "critical"
    assert body["priority_score"] == 0.92
    assert body["status"] == "complete"
