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

from regradar.agents.triage_agent import classify_filing

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
