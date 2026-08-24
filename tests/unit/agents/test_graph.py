"""Unit tests for the LangGraph stub nodes and triage routing decision.

Each node is called directly (not via a compiled graph) so it can be
tested in isolation, per AGENT-01's acceptance criteria.
"""

import uuid

import pytest

from regradar.agents.graph import (
    route_after_triage,
)
from regradar.agents.state import PipelineState
from regradar.models.enums import RiskLevel


def _make_state(risk_level: RiskLevel | None = None) -> PipelineState:
    return PipelineState(filing_id=uuid.uuid4(), raw_text="filing text", risk_level=risk_level)


@pytest.mark.parametrize(
    "risk_level",
    [RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL, None],
)
def test_route_after_triage_goes_to_retrieve_for_non_low_and_unclassified(risk_level) -> None:
    state = _make_state(risk_level=risk_level)

    assert route_after_triage(state) == "retrieve"


def test_route_after_triage_skips_retrieve_for_low_risk() -> None:
    state = _make_state(risk_level=RiskLevel.LOW)

    assert route_after_triage(state) == "analyze"
