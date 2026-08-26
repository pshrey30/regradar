"""Tests for POST /v1/filings/search."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from openai import APIConnectionError

from regradar.agents.state import RetrievedChunk
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.api.routers import filings as filings_module
from regradar.llm_routing.tiered_router import ModelChoice
from regradar.models.enums import ApiKeyRole

_TEST_MODEL_CHOICE = ModelChoice(
    tier="high", model="llama3.1", base_url="http://localhost:11434/v1", api_key="ollama-local"
)


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


def _mock_retrieval_db(monkeypatch: pytest.MonkeyPatch, *, filing_rows: list):
    """Mocks core.db.get_session_factory for the route's own db.execute
    call (looking up Filing rows for the retrieved chunks' entity names).
    retrieve_similar_filings itself is mocked separately, at module level.
    """
    import regradar.core.db as db_module

    mock_db = AsyncMock()
    filings_result = MagicMock()
    filings_scalars = MagicMock()
    filings_scalars.all = MagicMock(return_value=filing_rows)
    filings_result.scalars = MagicMock(return_value=filings_scalars)
    mock_db.execute = AsyncMock(return_value=filings_result)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)


def _filing_row(filing_id: uuid.UUID, entity_name: str = "Acme Corp"):
    filing = MagicMock()
    filing.id = filing_id
    filing.entity_name = entity_name
    return filing


def test_search_returns_403_for_executive_role(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.EXECUTIVE)
    # get_db is still resolved by FastAPI before the route body's role check
    # runs (dependency injection happens before the function executes), so
    # the DB must be mocked even though this test's route logic never uses it.
    _mock_retrieval_db(monkeypatch, filing_rows=[])

    response = TestClient(create_app()).post(
        "/v1/filings/search",
        json={"query": "what did the SEC say about widgets"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_search_without_auth_header_returns_401():
    response = TestClient(create_app()).post(
        "/v1/filings/search", json={"query": "anything"}
    )

    assert response.status_code == 401


def test_search_returns_structured_empty_result_when_no_matches(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST)
    _mock_retrieval_db(monkeypatch, filing_rows=[])
    monkeypatch.setattr(
        filings_module, "retrieve_similar_filings", AsyncMock(return_value=[])
    )

    response = TestClient(create_app()).post(
        "/v1/filings/search",
        json={"query": "nothing relevant exists"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sources"] == []
    assert body["answer"] is not None
    assert body["degraded"] is False


def test_search_returns_synthesized_answer_and_sources_on_success(
    monkeypatch: pytest.MonkeyPatch,
):
    filing_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.LEGAL_COUNSEL)
    monkeypatch.setattr(
        filings_module,
        "retrieve_similar_filings",
        AsyncMock(
            return_value=[
                RetrievedChunk(filing_id=filing_id, chunk_text="Widgets must be recalled.", score=0.9)
            ]
        ),
    )
    _mock_retrieval_db(monkeypatch, filing_rows=[_filing_row(filing_id, "Widget Co")])

    mock_llm_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Widgets were recalled."))]
    mock_llm_client.chat.completions.create.return_value = mock_response
    monkeypatch.setattr(filings_module, "select_model", lambda risk_level, task: _TEST_MODEL_CHOICE)
    monkeypatch.setattr(filings_module, "build_client", lambda choice: mock_llm_client)

    response = TestClient(create_app()).post(
        "/v1/filings/search",
        json={"query": "what happened with widgets", "top_k": 3},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Widgets were recalled."
    assert body["degraded"] is False
    assert len(body["sources"]) == 1
    assert body["sources"][0]["entity_name"] == "Widget Co"
    assert body["sources"][0]["filing_id"] == str(filing_id)
    assert body["sources"][0]["excerpt"] == "Widgets must be recalled."


def test_search_falls_back_to_degraded_when_llm_call_fails(monkeypatch: pytest.MonkeyPatch):
    filing_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    monkeypatch.setattr(
        filings_module,
        "retrieve_similar_filings",
        AsyncMock(
            return_value=[RetrievedChunk(filing_id=filing_id, chunk_text="Some text.", score=0.5)]
        ),
    )
    _mock_retrieval_db(monkeypatch, filing_rows=[_filing_row(filing_id)])

    mock_llm_client = MagicMock()
    mock_llm_client.chat.completions.create.side_effect = APIConnectionError(
        request=MagicMock()
    )
    monkeypatch.setattr(filings_module, "select_model", lambda risk_level, task: _TEST_MODEL_CHOICE)
    monkeypatch.setattr(filings_module, "build_client", lambda choice: mock_llm_client)

    response = TestClient(create_app()).post(
        "/v1/filings/search",
        json={"query": "anything"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] is None
    assert body["degraded"] is True
    assert len(body["sources"]) == 1
