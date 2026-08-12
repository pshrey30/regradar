"""Unit tests for the Triage Agent's HF classification call, risk heuristic,
and the real triage_node. All HTTP calls are mocked — see
test_triage_live_smoke.py for the one test allowed to hit the real API.
"""

import time
import uuid
from unittest.mock import ANY, MagicMock, patch

import httpx
import pytest

from regradar.agents.state import PipelineState
from regradar.agents.triage_agent import (
    ClassificationResult,
    SpotCheckResult,
    TriageClassificationError,
    _get_llm_client,
    classify_filing,
    derive_risk_level,
    spot_check_classification,
    triage_node,
)
from regradar.models.enums import FilingDomain, RiskLevel


def _mock_response(json_body: list[dict], status_code: int = 200) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = json_body
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=response
        )
    return response


HF_SUCCESS_BODY = [
    {"label": "financial", "score": 0.87},
    {"label": "other", "score": 0.08},
    {"label": "clinical", "score": 0.03},
    {"label": "environmental", "score": 0.02},
]


def test_classify_filing_returns_top_label_and_confidence() -> None:
    with patch("regradar.agents.triage_agent.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(HF_SUCCESS_BODY)

        result = classify_filing("Some filing text about financial disclosures.")

        assert result.domain == FilingDomain.FINANCIAL
        assert result.confidence == pytest.approx(0.87)
        assert result.raw_scores["financial"] == pytest.approx(0.87)


def test_classify_filing_sends_expected_candidate_labels_and_url() -> None:
    with patch("regradar.agents.triage_agent.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(HF_SUCCESS_BODY)

        classify_filing("some text")

        args, kwargs = mock_post.call_args
        assert args[0] == (
            "https://router.huggingface.co/hf-inference/models/facebook/bart-large-mnli"
        )
        assert kwargs["json"]["parameters"]["candidate_labels"] == [
            "financial",
            "clinical",
            "environmental",
            "other",
        ]


def test_classify_filing_retries_once_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with patch("regradar.agents.triage_agent.httpx.post") as mock_post:
        mock_post.side_effect = [
            httpx.RequestError("connection failed", request=MagicMock()),
            _mock_response(HF_SUCCESS_BODY),
        ]

        result = classify_filing("some text")

        assert result.domain == FilingDomain.FINANCIAL
        assert mock_post.call_count == 2


def test_classify_filing_raises_after_both_attempts_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with patch("regradar.agents.triage_agent.httpx.post") as mock_post:
        mock_post.side_effect = httpx.RequestError("connection failed", request=MagicMock())

        with pytest.raises(TriageClassificationError):
            classify_filing("some text")

        assert mock_post.call_count == 2


def test_derive_risk_level_critical_keyword_overrides_high_confidence() -> None:
    text = "The company disclosed a material weakness in internal controls."

    result = derive_risk_level(FilingDomain.FINANCIAL, confidence=0.95, text=text)

    assert result == RiskLevel.CRITICAL


def test_derive_risk_level_high_keyword() -> None:
    text = "The FDA issued a warning letter regarding manufacturing practices."

    result = derive_risk_level(FilingDomain.CLINICAL, confidence=0.9, text=text)

    assert result == RiskLevel.HIGH


def test_derive_risk_level_low_confidence_with_no_keywords_is_medium() -> None:
    text = "Routine quarterly filing with no notable events."

    result = derive_risk_level(FilingDomain.FINANCIAL, confidence=0.3, text=text)

    assert result == RiskLevel.MEDIUM


def test_derive_risk_level_confident_and_clean_is_low() -> None:
    text = "Routine quarterly filing with no notable events."

    result = derive_risk_level(FilingDomain.FINANCIAL, confidence=0.9, text=text)

    assert result == RiskLevel.LOW


def test_derive_risk_level_low_confidence_but_critical_keyword_is_still_critical() -> None:
    text = "Preliminary indication of possible fraud under review."

    result = derive_risk_level(FilingDomain.FINANCIAL, confidence=0.2, text=text)

    assert result == RiskLevel.CRITICAL


def _mock_openai_client(content: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    client.chat.completions.create.return_value = response
    return client


def test_spot_check_classification_returns_parsed_result() -> None:
    content = (
        '{"domain": "financial", "risk_level": "high", '
        '"reasoning": "Material weakness disclosed."}'
    )
    with patch(
        "regradar.agents.triage_agent._get_llm_client",
        return_value=(_mock_openai_client(content), "llama3.1"),
    ):
        result = spot_check_classification("some filing text")

    assert result == SpotCheckResult(
        domain=FilingDomain.FINANCIAL,
        risk_level=RiskLevel.HIGH,
        reasoning="Material weakness disclosed.",
    )


def test_spot_check_classification_returns_none_on_malformed_json() -> None:
    with patch(
        "regradar.agents.triage_agent._get_llm_client",
        return_value=(_mock_openai_client("not valid json"), "llama3.1"),
    ):
        result = spot_check_classification("some filing text")

    assert result is None


def test_spot_check_classification_returns_none_on_request_error() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("connection refused")
    with patch("regradar.agents.triage_agent._get_llm_client", return_value=(client, "llama3.1")):
        result = spot_check_classification("some filing text")

    assert result is None


def test_get_llm_client_uses_local_settings_when_use_local_llm_true() -> None:
    fake_settings = MagicMock()
    fake_settings.use_local_llm = True
    fake_settings.local_llm_base_url = "http://localhost:11434/v1"
    fake_settings.local_llm_model = "llama3.1"

    with patch("regradar.agents.triage_agent.get_settings", return_value=fake_settings):
        with patch("regradar.agents.triage_agent.OpenAI") as mock_openai_cls:
            client, model = _get_llm_client()

    mock_openai_cls.assert_called_once_with(base_url="http://localhost:11434/v1", api_key=ANY)
    assert model == "llama3.1"


def test_get_llm_client_uses_real_openai_when_use_local_llm_false() -> None:
    fake_settings = MagicMock()
    fake_settings.use_local_llm = False
    fake_settings.tier_high_model = "gpt-4o"
    fake_settings.openai_api_key.get_secret_value.return_value = "sk-real"

    with patch("regradar.agents.triage_agent.get_settings", return_value=fake_settings):
        with patch("regradar.agents.triage_agent.OpenAI") as mock_openai_cls:
            client, model = _get_llm_client()

    mock_openai_cls.assert_called_once_with(api_key="sk-real")
    assert model == "gpt-4o"


def _make_state() -> PipelineState:
    return PipelineState(filing_id=uuid.uuid4(), raw_text="Routine filing text.")


def test_triage_node_populates_state_on_success() -> None:
    fake_result = ClassificationResult(
        domain=FilingDomain.FINANCIAL, confidence=0.9, raw_scores={"financial": 0.9}
    )
    with patch("regradar.agents.triage_agent.classify_filing", return_value=fake_result):
        state = triage_node(_make_state())

    assert state.domain == FilingDomain.FINANCIAL
    assert state.classification_confidence == 0.9
    assert state.risk_level == RiskLevel.LOW


def test_triage_node_leaves_state_unclassified_on_failure() -> None:
    with patch(
        "regradar.agents.triage_agent.classify_filing",
        side_effect=TriageClassificationError("boom"),
    ):
        state = triage_node(_make_state())

    assert state.domain is None
    assert state.classification_confidence is None
    assert state.risk_level is None
