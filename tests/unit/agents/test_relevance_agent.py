"""Unit tests for the Relevance Agent's org-specific impact scoring.

The OpenAI-compatible client is always mocked — no real Ollama or OpenAI
call in these tests. See test_relevance_agent_live_smoke.py for the one
test allowed to hit the real local Ollama server.
"""

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

from regradar.agents.relevance_agent import (
    RelevanceError,
    _validate_relevance,
    compute_priority_score,
    relevance_node,
)
from regradar.agents.state import ExtractionResult, OrgProfileSnapshot, PipelineState
from regradar.llm_routing.tiered_router import ModelChoice
from regradar.models.enums import FilingDomain, RiskLevel

VALID_RELEVANCE_JSON = {
    "relevance_score": 0.9,
    "matched_signals": {
        "watchlist_entities": ["Acme Corp"],
        "products": ["insulin pumps"],
        "industry_alignment": "Same medical device industry.",
    },
    "rationale": "Acme Corp, on your watchlist, had an FDA recall affecting insulin pumps.",
    "recommended_action": "Review your own insulin pump supply chain for the same defect.",
}


def _mock_openai_client(content: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    client.chat.completions.create.return_value = response
    return client


def _fake_model_choice(model: str = "llama3.1") -> ModelChoice:
    return ModelChoice(tier="high", model=model, base_url="http://localhost:11434/v1", api_key="ollama-local")


def _make_state_with_extraction(org_profile: OrgProfileSnapshot | None = None) -> PipelineState:
    return PipelineState(
        filing_id=uuid.uuid4(),
        raw_text="Full filing text.",
        domain=FilingDomain.CLINICAL,
        risk_level=RiskLevel.HIGH,
        extraction=ExtractionResult(
            obligations=[],
            deadlines=[],
            risk_flags=["recall"],
            affected_products=["insulin pumps"],
            key_entities=["Acme Corp"],
            competitor_mentions=["Acme Corp"],
        ),
        org_profile=org_profile
        or OrgProfileSnapshot(
            industry="medical device manufacturing",
            watchlist_entities=["Acme Corp"],
            products=["insulin pumps"],
        ),
    )


def test_relevance_node_populates_relevance_on_valid_response() -> None:
    content = json.dumps(VALID_RELEVANCE_JSON)
    with patch(
        "regradar.agents.relevance_agent._get_llm_client",
        return_value=(_mock_openai_client(content), "llama3.1", _fake_model_choice()),
    ):
        result = relevance_node(_make_state_with_extraction())

    assert result.relevance is not None
    assert result.relevance.relevance_score == 0.9
    assert result.relevance.matched_signals["watchlist_entities"] == ["Acme Corp"]
    assert "Acme Corp" in result.relevance.rationale
    assert result.relevance.model_used == "llama3.1"


def test_relevance_node_retries_once_on_malformed_json_then_succeeds() -> None:
    valid_content = json.dumps(VALID_RELEVANCE_JSON)
    client = MagicMock()
    malformed_response = MagicMock()
    malformed_response.choices = [MagicMock(message=MagicMock(content="not valid json"))]
    valid_response = MagicMock()
    valid_response.choices = [MagicMock(message=MagicMock(content=valid_content))]
    client.chat.completions.create.side_effect = [malformed_response, valid_response]

    with patch(
        "regradar.agents.relevance_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        result = relevance_node(_make_state_with_extraction())

    assert result.relevance is not None
    assert result.relevance.relevance_score == 0.9
    assert client.chat.completions.create.call_count == 2


def test_relevance_node_falls_back_to_neutral_default_after_two_failures() -> None:
    client = MagicMock()
    malformed_response = MagicMock()
    malformed_response.choices = [MagicMock(message=MagicMock(content="still not valid json"))]
    client.chat.completions.create.return_value = malformed_response

    with patch(
        "regradar.agents.relevance_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        result = relevance_node(_make_state_with_extraction())

    assert result.relevance is not None
    assert result.relevance.relevance_score == 0.5
    assert result.relevance.matched_signals == {}


def test_relevance_node_falls_back_to_neutral_default_when_extraction_missing() -> None:
    state = PipelineState(
        filing_id=uuid.uuid4(),
        raw_text="text",
        domain=FilingDomain.FINANCIAL,
        risk_level=RiskLevel.LOW,
        extraction=None,
    )
    result = relevance_node(state)
    assert result.relevance is not None
    assert result.relevance.relevance_score == 0.5


def test_relevance_node_handles_missing_org_profile() -> None:
    content = json.dumps(VALID_RELEVANCE_JSON)
    state = _make_state_with_extraction(org_profile=None)
    with patch(
        "regradar.agents.relevance_agent._get_llm_client",
        return_value=(_mock_openai_client(content), "llama3.1", _fake_model_choice()),
    ):
        result = relevance_node(state)
    assert result.relevance is not None


def test_validate_relevance_rejects_out_of_range_score() -> None:
    out_of_range = dict(VALID_RELEVANCE_JSON, relevance_score=8.0)
    with pytest.raises(RelevanceError, match="between 0.0 and 1.0"):
        _validate_relevance(out_of_range)


def test_validate_relevance_accepts_boundary_scores() -> None:
    _validate_relevance(dict(VALID_RELEVANCE_JSON, relevance_score=0.0))
    _validate_relevance(dict(VALID_RELEVANCE_JSON, relevance_score=1.0))


def test_relevance_node_falls_back_to_neutral_default_when_score_out_of_range_twice() -> None:
    out_of_range_content = json.dumps(dict(VALID_RELEVANCE_JSON, relevance_score=8.0))
    client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=out_of_range_content))]
    client.chat.completions.create.return_value = response

    with patch(
        "regradar.agents.relevance_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        result = relevance_node(_make_state_with_extraction())

    assert result.relevance is not None
    assert result.relevance.relevance_score == 0.5
    assert result.relevance.matched_signals == {}
    assert client.chat.completions.create.call_count == 2


def test_compute_priority_score_weights_severity_and_relevance_equally() -> None:
    assert compute_priority_score(RiskLevel.CRITICAL, 0.0) == 50.0
    assert compute_priority_score(RiskLevel.LOW, 1.0) == 50.0
    assert compute_priority_score(RiskLevel.CRITICAL, 1.0) == 100.0
    assert compute_priority_score(RiskLevel.LOW, 0.0) == 0.0
    assert compute_priority_score(RiskLevel.HIGH, 0.5) == round(100 * (0.5 * (2 / 3) + 0.5 * 0.5), 1)
