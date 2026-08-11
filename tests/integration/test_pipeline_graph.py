"""End-to-end integration test for the compiled LangGraph pipeline graph.

No real database is needed here — every node is a stub operating purely
on in-memory PipelineState. This test's job is to confirm the graph
wiring itself (including the conditional retrieve-skip edge) behaves as
AGENT-01 specifies, which unit-testing each node in isolation can't show.
"""

import uuid

import pytest

from regradar.agents import graph as graph_module
from regradar.agents.state import PipelineState
from regradar.models.enums import RiskLevel


def _make_state(risk_level: RiskLevel | None = None) -> PipelineState:
    return PipelineState(filing_id=uuid.uuid4(), raw_text="filing text", risk_level=risk_level)


def test_graph_runs_end_to_end_and_reaches_deliver() -> None:
    compiled = graph_module.build_graph()
    state = _make_state(risk_level=RiskLevel.HIGH)

    result = compiled.invoke(state)

    assert result["filing_id"] == state.filing_id
    assert result["risk_level"] == RiskLevel.HIGH


def test_unclassified_filing_takes_the_retrieve_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    original_retrieve = graph_module.retrieve_node

    def _spy_retrieve(state: PipelineState) -> PipelineState:
        calls.append("retrieve")
        return original_retrieve(state)

    monkeypatch.setattr(graph_module, "retrieve_node", _spy_retrieve)
    compiled = graph_module.build_graph()

    compiled.invoke(_make_state(risk_level=None))

    assert calls == ["retrieve"]


def test_low_risk_filing_skips_the_retrieve_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    original_retrieve = graph_module.retrieve_node

    def _spy_retrieve(state: PipelineState) -> PipelineState:
        calls.append("retrieve")
        return original_retrieve(state)

    monkeypatch.setattr(graph_module, "retrieve_node", _spy_retrieve)
    compiled = graph_module.build_graph()

    compiled.invoke(_make_state(risk_level=RiskLevel.LOW))

    assert calls == []
