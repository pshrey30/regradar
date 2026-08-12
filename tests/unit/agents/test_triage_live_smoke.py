"""Live smoke test for the Triage Agent's HF classification call.

This is the ONE test allowed to hit the real Hugging Face API. It's
marked `live` and excluded from default pytest runs (see pyproject.toml's
addopts) — run it explicitly with `pytest -m live` when you actually want
to verify the live integration. Never wire this into CI or any
automatically-scheduled run: this project's cost/supervision policy is
that paid API calls only happen when a human explicitly asks for one.

This asserts a loose accuracy bar (>=80% on a 12-example set) — it is a
smoke test that classify_filing() behaves sensibly against a small hand-
labeled set, not the ticket's rigorous 90%/100-filing benchmark. That
real benchmark is EVAL-03's job, once a proper labeled set exists.
"""

import json
from pathlib import Path

import pytest

from regradar.agents.triage_agent import classify_filing, spot_check_classification

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "triage_smoke_set.json"


@pytest.mark.live
def test_classify_filing_live_smoke_test() -> None:
    examples = json.loads(FIXTURE_PATH.read_text())

    correct = 0
    for example in examples:
        result = classify_filing(example["text"])
        if result.domain.value == example["expected_domain"]:
            correct += 1

    accuracy = correct / len(examples)
    assert accuracy >= 0.8, f"Live smoke test accuracy {accuracy:.2%} below 80% threshold"


@pytest.mark.live
def test_spot_check_classification_live_smoke_test() -> None:
    """Requires Ollama running locally with llama3.1 pulled, and
    USE_LOCAL_LLM=true in .env (both already true in this repo's local
    setup). Start Ollama manually before running this test explicitly
    (`ollama serve`, or the background-launch pattern used during
    AGENT-03's design verification) — do not leave it running afterward.
    """
    result = spot_check_classification(
        "The company disclosed a material weakness in internal controls "
        "over financial reporting and must remediate within 90 days."
    )

    assert result is not None
    assert result.domain.value in ("financial", "clinical", "environmental", "other")
    assert result.risk_level.value in ("low", "medium", "high", "critical")
    assert len(result.reasoning) > 0
