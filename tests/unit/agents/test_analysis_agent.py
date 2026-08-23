"""Unit tests for the Analysis Agent's structured extraction call.

The OpenAI-compatible client is always mocked — no real Ollama or
OpenAI call in these tests. See test_analysis_agent_live_smoke.py (this
same file, marked @pytest.mark.live) for the one test allowed to hit the
real local Ollama server.
"""

import json
import uuid
from unittest.mock import MagicMock, patch

from openai import APIConnectionError

from regradar.agents.analysis_agent import analyze_node
from regradar.agents.state import PipelineState
from regradar.llm_routing.tiered_router import ModelChoice
from regradar.models.enums import RiskLevel
from regradar.rag.chunking import Chunk

VALID_EXTRACTION_JSON = {
    "obligations": [
        {
            "description": "File annual compliance certification by January 15, 2027.",
            "source_chunk_index": 0,
        }
    ],
    "deadlines": [{"description": "Annual compliance certification", "date": "2027-01-15"}],
    "risk_flags": ["material weakness"],
    "affected_products": ["Product X"],
    "key_entities": ["Acme Corp"],
    "competitor_mentions": [],
}


def _mock_openai_client(content: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    client.chat.completions.create.return_value = response
    return client


def _fake_model_choice(model: str = "llama3.1") -> ModelChoice:
    return ModelChoice(tier="high", model=model, base_url="http://localhost:11434/v1", api_key="ollama-local")


def _make_state_with_chunks(chunk_count: int = 1) -> PipelineState:
    chunks = [
        Chunk(
            chunk_index=i,
            chunk_text=f"Chunk {i} text about compliance obligations.",
            section_reference=None,
            token_count=6,
            is_table=False,
        )
        for i in range(chunk_count)
    ]
    return PipelineState(
        filing_id=uuid.uuid4(), raw_text="Full filing text.", chunks=chunks
    )


def test_analyze_node_populates_extraction_on_valid_response() -> None:
    content = json.dumps(VALID_EXTRACTION_JSON)
    with patch(
        "regradar.agents.analysis_agent._get_llm_client",
        return_value=(_mock_openai_client(content), "llama3.1", _fake_model_choice()),
    ):
        result = analyze_node(_make_state_with_chunks())

    assert result.extraction is not None
    assert result.extraction.obligations == VALID_EXTRACTION_JSON["obligations"]
    assert result.extraction.deadlines == VALID_EXTRACTION_JSON["deadlines"]
    assert result.extraction.risk_flags == ["material weakness"]
    assert result.extraction.model_used == "llama3.1"


def test_analyze_node_retries_once_on_malformed_json_then_succeeds() -> None:
    valid_content = json.dumps(VALID_EXTRACTION_JSON)
    client = MagicMock()
    malformed_response = MagicMock()
    malformed_response.choices = [MagicMock(message=MagicMock(content="not valid json"))]
    valid_response = MagicMock()
    valid_response.choices = [MagicMock(message=MagicMock(content=valid_content))]
    client.chat.completions.create.side_effect = [malformed_response, valid_response]

    with patch(
        "regradar.agents.analysis_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        result = analyze_node(_make_state_with_chunks())

    assert result.extraction is not None
    assert client.chat.completions.create.call_count == 2


def test_analyze_node_leaves_extraction_none_after_two_malformed_responses() -> None:
    client = MagicMock()
    malformed_response = MagicMock()
    malformed_response.choices = [MagicMock(message=MagicMock(content="not valid json"))]
    client.chat.completions.create.return_value = malformed_response

    with patch(
        "regradar.agents.analysis_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        result = analyze_node(_make_state_with_chunks())

    assert result.extraction is None
    assert client.chat.completions.create.call_count == 2


def test_analyze_node_rejects_out_of_range_source_chunk_index_and_retries() -> None:
    bad_json = dict(VALID_EXTRACTION_JSON)
    bad_json["obligations"] = [
        {"description": "Some obligation.", "source_chunk_index": 99}
    ]
    valid_content = json.dumps(VALID_EXTRACTION_JSON)
    client = MagicMock()
    bad_response = MagicMock()
    bad_response.choices = [MagicMock(message=MagicMock(content=json.dumps(bad_json)))]
    good_response = MagicMock()
    good_response.choices = [MagicMock(message=MagicMock(content=valid_content))]
    client.chat.completions.create.side_effect = [bad_response, good_response]

    with patch(
        "regradar.agents.analysis_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        # Only 1 chunk exists (index 0) in _make_state_with_chunks(1); index 99 is invalid.
        result = analyze_node(_make_state_with_chunks(chunk_count=1))

    assert result.extraction is not None
    assert client.chat.completions.create.call_count == 2


def test_analyze_node_leaves_extraction_none_on_wrong_typed_field_without_crashing() -> None:
    """Reproduces the reviewer's finding: a response with all required keys
    present but `obligations: null` (or similarly wrong-typed) must not
    raise an unhandled TypeError out of analyze_node — it must be treated
    as a validation failure that retries then gives up, leaving
    state.extraction as None."""
    bad_json = dict(VALID_EXTRACTION_JSON)
    bad_json["obligations"] = None
    client = MagicMock()
    bad_response = MagicMock()
    bad_response.choices = [MagicMock(message=MagicMock(content=json.dumps(bad_json)))]
    client.chat.completions.create.return_value = bad_response

    with patch(
        "regradar.agents.analysis_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        result = analyze_node(_make_state_with_chunks())

    assert result.extraction is None
    assert client.chat.completions.create.call_count == 2


def test_analyze_node_with_no_chunks_leaves_extraction_none() -> None:
    state = PipelineState(filing_id=uuid.uuid4(), raw_text="", chunks=None)

    with patch("regradar.agents.analysis_agent._get_llm_client") as mock_get_client:
        result = analyze_node(state)

    assert result.extraction is None
    mock_get_client.assert_not_called()


def test_analyze_node_passes_state_risk_level_to_get_llm_client() -> None:
    content = json.dumps(VALID_EXTRACTION_JSON)
    state = _make_state_with_chunks()
    state = state.model_copy(update={"risk_level": RiskLevel.CRITICAL})

    with patch(
        "regradar.agents.analysis_agent._get_llm_client",
        return_value=(_mock_openai_client(content), "llama3.1", _fake_model_choice()),
    ) as mock_get_client:
        analyze_node(state)

    mock_get_client.assert_called_once_with(RiskLevel.CRITICAL)


def test_analyze_node_falls_back_to_other_tier_on_connection_error() -> None:
    content = json.dumps(VALID_EXTRACTION_JSON)
    primary_client = MagicMock()
    primary_client.chat.completions.create.side_effect = APIConnectionError(
        request=MagicMock()
    )
    fallback_client = _mock_openai_client(content)

    primary_choice = ModelChoice(
        tier="high", model="llama3.1", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )
    fallback_choice = ModelChoice(
        tier="low", model="llama3.2:1b", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )

    with (
        patch(
            "regradar.agents.analysis_agent._get_llm_client",
            return_value=(primary_client, "llama3.1", primary_choice),
        ),
        patch(
            "regradar.agents.analysis_agent.other_tier_choice",
            return_value=fallback_choice,
        ),
        patch(
            "regradar.agents.analysis_agent.build_client",
            return_value=fallback_client,
        ),
    ):
        result = analyze_node(_make_state_with_chunks())

    assert result.extraction is not None
    assert result.extraction.model_used == "llama3.2:1b"


def test_analyze_node_gives_up_after_fallback_also_fails() -> None:
    primary_client = MagicMock()
    primary_client.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())
    fallback_client = MagicMock()
    fallback_client.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())

    primary_choice = ModelChoice(
        tier="high", model="llama3.1", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )
    fallback_choice = ModelChoice(
        tier="low", model="llama3.2:1b", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )

    with (
        patch(
            "regradar.agents.analysis_agent._get_llm_client",
            return_value=(primary_client, "llama3.1", primary_choice),
        ),
        patch(
            "regradar.agents.analysis_agent.other_tier_choice",
            return_value=fallback_choice,
        ),
        patch(
            "regradar.agents.analysis_agent.build_client",
            return_value=fallback_client,
        ),
    ):
        result = analyze_node(_make_state_with_chunks())

    assert result.extraction is None
