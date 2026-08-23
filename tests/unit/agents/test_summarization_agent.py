"""Unit tests for the Summarization Agent's persona-brief generation call.

The OpenAI-compatible client is always mocked — no real Ollama or OpenAI
call in these tests, mirroring test_analysis_agent.py.
"""

import json
import uuid
from unittest.mock import MagicMock, patch

from openai import APIConnectionError

from regradar.agents.state import ExtractionResult, PipelineState
from regradar.agents.summarization_agent import summarize_node
from regradar.llm_routing.tiered_router import ModelChoice
from regradar.models.enums import FilingDomain, RiskLevel

VALID_SUMMARIZATION_JSON = {
    "executive_brief": (
        "Acme Corp filed an annual compliance certification. The filing flags a "
        "material weakness in internal controls. A remediation deadline of "
        "January 15, 2027 applies."
    ),
    "cco_summary": "Acme Corp: material weakness flagged, high risk, remediation due Jan 2027.",
    "analyst_summary": (
        "- File annual compliance certification (due 2027-01-15)\n"
        "- Remediate flagged material weakness"
    ),
}


def _mock_openai_client(content: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    client.chat.completions.create.return_value = response
    return client


def _fake_model_choice(model: str = "llama3.1") -> ModelChoice:
    return ModelChoice(tier="high", model=model, base_url="http://localhost:11434/v1", api_key="ollama-local")


def _make_state_with_extraction(obligation_count: int = 1) -> PipelineState:
    extraction = ExtractionResult(
        obligations=[
            {"description": f"Obligation {i}.", "source_chunk_index": 0}
            for i in range(obligation_count)
        ],
        deadlines=[{"description": "Annual compliance certification", "date": "2027-01-15"}],
        risk_flags=["material weakness"],
        affected_products=[],
        key_entities=["Acme Corp"],
        competitor_mentions=[],
        model_used="llama3.1",
    )
    return PipelineState(
        filing_id=uuid.uuid4(),
        raw_text="Full filing text.",
        domain=FilingDomain.FINANCIAL,
        risk_level=RiskLevel.HIGH,
        extraction=extraction,
    )


def test_summarize_node_populates_briefs_on_valid_response() -> None:
    content = json.dumps(VALID_SUMMARIZATION_JSON)
    state = _make_state_with_extraction(obligation_count=1)

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(_mock_openai_client(content), "llama3.1", _fake_model_choice()),
    ):
        result = summarize_node(state)

    assert result.briefs is not None
    assert result.briefs.executive_brief == VALID_SUMMARIZATION_JSON["executive_brief"]
    assert result.briefs.cco_summary == VALID_SUMMARIZATION_JSON["cco_summary"]
    assert result.briefs.analyst_summary == VALID_SUMMARIZATION_JSON["analyst_summary"]
    assert result.briefs.model_used == "llama3.1"
    # engineer_summary is built deterministically, not from the LLM response.
    assert str(state.filing_id) in result.briefs.engineer_summary
    assert "domain=financial" in result.briefs.engineer_summary
    assert "risk_level=high" in result.briefs.engineer_summary
    assert "obligations_extracted=1" in result.briefs.engineer_summary


def test_summarize_node_retries_once_on_malformed_json_then_succeeds() -> None:
    valid_content = json.dumps(VALID_SUMMARIZATION_JSON)
    client = MagicMock()
    malformed_response = MagicMock()
    malformed_response.choices = [MagicMock(message=MagicMock(content="not valid json"))]
    valid_response = MagicMock()
    valid_response.choices = [MagicMock(message=MagicMock(content=valid_content))]
    client.chat.completions.create.side_effect = [malformed_response, valid_response]

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is not None
    assert client.chat.completions.create.call_count == 2


def test_summarize_node_leaves_briefs_none_after_two_malformed_responses() -> None:
    client = MagicMock()
    malformed_response = MagicMock()
    malformed_response.choices = [MagicMock(message=MagicMock(content="not valid json"))]
    client.chat.completions.create.return_value = malformed_response

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is None
    assert client.chat.completions.create.call_count == 2


def test_summarize_node_retries_when_executive_brief_sentence_count_out_of_range() -> None:
    bad_json = dict(VALID_SUMMARIZATION_JSON)
    bad_json["executive_brief"] = "Too short."
    good_content = json.dumps(VALID_SUMMARIZATION_JSON)
    client = MagicMock()
    bad_response = MagicMock()
    bad_response.choices = [MagicMock(message=MagicMock(content=json.dumps(bad_json)))]
    good_response = MagicMock()
    good_response.choices = [MagicMock(message=MagicMock(content=good_content))]
    client.chat.completions.create.side_effect = [bad_response, good_response]

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is not None
    assert client.chat.completions.create.call_count == 2


def test_summarize_node_retries_when_cco_summary_exceeds_fifty_words() -> None:
    bad_json = dict(VALID_SUMMARIZATION_JSON)
    bad_json["cco_summary"] = " ".join(["word"] * 60)
    good_content = json.dumps(VALID_SUMMARIZATION_JSON)
    client = MagicMock()
    bad_response = MagicMock()
    bad_response.choices = [MagicMock(message=MagicMock(content=json.dumps(bad_json)))]
    good_response = MagicMock()
    good_response.choices = [MagicMock(message=MagicMock(content=good_content))]
    client.chat.completions.create.side_effect = [bad_response, good_response]

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is not None
    assert client.chat.completions.create.call_count == 2


def test_summarize_node_leaves_briefs_none_on_wrong_typed_field_without_crashing() -> None:
    bad_json = dict(VALID_SUMMARIZATION_JSON)
    bad_json["analyst_summary"] = None
    client = MagicMock()
    bad_response = MagicMock()
    bad_response.choices = [MagicMock(message=MagicMock(content=json.dumps(bad_json)))]
    client.chat.completions.create.return_value = bad_response

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is None
    assert client.chat.completions.create.call_count == 2


def test_summarize_node_accepts_executive_brief_with_abbreviation_periods() -> None:
    """A genuinely 3-sentence executive_brief containing common regulatory-prose
    abbreviations (U.S., Corp., Jan., Sec.) must not be mis-counted as more than
    3 sentences and wrongly rejected/retried."""
    abbreviation_json = dict(VALID_SUMMARIZATION_JSON)
    abbreviation_json["executive_brief"] = (
        "The U.S. Securities and Exchange Commission issued a notice. Acme Corp. "
        "must remediate a material weakness by Jan. 15, 2027. The filing affects "
        "Sec. 404 controls."
    )
    content = json.dumps(abbreviation_json)
    client = _mock_openai_client(content)

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is not None
    assert result.briefs.executive_brief == abbreviation_json["executive_brief"]
    # No retry was needed — the abbreviation periods must not have inflated
    # the sentence count past the valid 3-5 range.
    assert client.chat.completions.create.call_count == 1


def test_summarize_node_with_no_extraction_leaves_briefs_none() -> None:
    state = PipelineState(filing_id=uuid.uuid4(), raw_text="", extraction=None)

    with patch("regradar.agents.summarization_agent._get_llm_client") as mock_get_client:
        result = summarize_node(state)

    assert result.briefs is None
    mock_get_client.assert_not_called()


def test_summarize_node_passes_state_risk_level_to_get_llm_client() -> None:
    content = json.dumps(VALID_SUMMARIZATION_JSON)
    state = _make_state_with_extraction()

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(_mock_openai_client(content), "llama3.1", _fake_model_choice()),
    ) as mock_get_client:
        summarize_node(state)

    mock_get_client.assert_called_once_with(state.risk_level)


def test_summarize_node_falls_back_to_other_tier_on_connection_error() -> None:
    content = json.dumps(VALID_SUMMARIZATION_JSON)
    primary_client = MagicMock()
    primary_client.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())
    fallback_client = _mock_openai_client(content)

    primary_choice = ModelChoice(
        tier="high", model="llama3.1", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )
    fallback_choice = ModelChoice(
        tier="low", model="llama3.2:1b", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )

    with (
        patch(
            "regradar.agents.summarization_agent._get_llm_client",
            return_value=(primary_client, "llama3.1", primary_choice),
        ),
        patch(
            "regradar.agents.summarization_agent.other_tier_choice",
            return_value=fallback_choice,
        ),
        patch(
            "regradar.agents.summarization_agent.build_client",
            return_value=fallback_client,
        ),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is not None
    assert result.briefs.model_used == "llama3.2:1b"


def test_summarize_node_gives_up_after_fallback_also_fails() -> None:
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
            "regradar.agents.summarization_agent._get_llm_client",
            return_value=(primary_client, "llama3.1", primary_choice),
        ),
        patch(
            "regradar.agents.summarization_agent.other_tier_choice",
            return_value=fallback_choice,
        ),
        patch(
            "regradar.agents.summarization_agent.build_client",
            return_value=fallback_client,
        ),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is None
