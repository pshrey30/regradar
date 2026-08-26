"""Tests for GET /v1/filings/{id}/brief."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.models.enums import ApiKeyRole


def _authenticated_key_row(role: ApiKeyRole):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.organization_id = uuid.uuid4()
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


def _brief_row():
    brief = MagicMock()
    brief.executive_brief = "Executive brief text."
    brief.cco_summary = "CCO summary text."
    brief.analyst_summary = "Analyst summary text."
    brief.engineer_summary = "Engineer summary text."
    return brief


def _mock_brief_db(monkeypatch: pytest.MonkeyPatch, *, filing, brief=None):
    """filing=None simulates a nonexistent filing (db.get returns None).
    brief=None (with a real filing) simulates a filing with no brief yet.
    """
    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)

    brief_result = MagicMock()
    brief_result.scalar_one_or_none = MagicMock(return_value=brief)
    mock_db.execute = AsyncMock(return_value=brief_result)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)


def _get_brief(client: TestClient, filing_id: uuid.UUID, persona: str | None = None):
    params = {"persona": persona} if persona is not None else None
    return client.get(
        f"/v1/filings/{filing_id}/brief",
        params=params,
        headers={"Authorization": "Bearer rr_test-key"},
    )


def test_brief_without_auth_header_returns_401():
    response = TestClient(create_app()).get(f"/v1/filings/{uuid.uuid4()}/brief")

    assert response.status_code == 401


def test_brief_returns_404_for_unknown_filing(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_brief_db(monkeypatch, filing=None)

    response = _get_brief(TestClient(create_app()), uuid.uuid4())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "filing_not_found"


def test_brief_returns_404_when_no_brief_exists_yet(monkeypatch: pytest.MonkeyPatch):
    filing_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_brief_db(monkeypatch, filing=MagicMock(id=filing_id), brief=None)

    response = _get_brief(TestClient(create_app()), filing_id)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "brief_not_found"


def test_brief_defaults_to_executive_brief_when_persona_omitted(
    monkeypatch: pytest.MonkeyPatch,
):
    filing_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_brief_db(monkeypatch, filing=MagicMock(id=filing_id), brief=_brief_row())

    response = _get_brief(TestClient(create_app()), filing_id)

    assert response.status_code == 200
    body = response.json()
    assert body["persona"] == "executive"
    assert body["summary"] == "Executive brief text."


@pytest.mark.parametrize(
    ("persona", "expected_summary"),
    [
        ("cco", "CCO summary text."),
        ("analyst", "Analyst summary text."),
        ("engineer", "Engineer summary text."),
    ],
)
def test_brief_returns_requested_persona_for_permitted_role(
    monkeypatch: pytest.MonkeyPatch, persona: str, expected_summary: str
):
    filing_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST)
    _mock_brief_db(monkeypatch, filing=MagicMock(id=filing_id), brief=_brief_row())

    response = _get_brief(TestClient(create_app()), filing_id, persona=persona)

    assert response.status_code == 200
    body = response.json()
    assert body["persona"] == persona
    assert body["summary"] == expected_summary


def test_brief_invalid_persona_returns_422(monkeypatch: pytest.MonkeyPatch):
    filing_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_brief_db(monkeypatch, filing=MagicMock(id=filing_id), brief=_brief_row())

    response = _get_brief(TestClient(create_app()), filing_id, persona="ceo")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_persona"
    assert "cco" in body["error"]["message"]
    assert "analyst" in body["error"]["message"]
    assert "engineer" in body["error"]["message"]


@pytest.mark.parametrize("requested_persona", [None, "analyst", "engineer"])
def test_executive_role_always_gets_cco_regardless_of_requested_persona(
    monkeypatch: pytest.MonkeyPatch, requested_persona: str | None
):
    filing_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.EXECUTIVE)
    _mock_brief_db(monkeypatch, filing=MagicMock(id=filing_id), brief=_brief_row())

    response = _get_brief(TestClient(create_app()), filing_id, persona=requested_persona)

    assert response.status_code == 200
    body = response.json()
    assert body["persona"] == "cco"
    assert body["summary"] == "CCO summary text."


def test_executive_role_with_invalid_persona_still_returns_422(
    monkeypatch: pytest.MonkeyPatch,
):
    """Validation happens before role-based narrowing — Executive doesn't
    get a free pass on a genuinely invalid persona value.
    """
    filing_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.EXECUTIVE)
    _mock_brief_db(monkeypatch, filing=MagicMock(id=filing_id), brief=_brief_row())

    response = _get_brief(TestClient(create_app()), filing_id, persona="ceo")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_persona"
