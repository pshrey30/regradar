"""Unit tests for the Triage Agent's HF classification call, risk heuristic,
and the real triage_node. All HTTP calls are mocked — see
test_triage_live_smoke.py for the one test allowed to hit the real API.
"""

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from regradar.agents.triage_agent import (
    ClassificationResult,
    TriageClassificationError,
    classify_filing,
)
from regradar.models.enums import FilingDomain


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
