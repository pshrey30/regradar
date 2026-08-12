"""End-to-end integration test for the compiled LangGraph pipeline graph.

No real database is needed here — every node besides triage is a stub
operating purely on in-memory PipelineState. triage_node is real as of
AGENT-02 and would otherwise make a live Hugging Face API call on every
run of this test; per this project's cost/supervision policy, automated
tests never call a paid API by default, so every test here monkeypatches
graph_module.triage_node with a controllable fake that sets risk_level
directly (simulating triage having already run) — the same pattern
already used for retrieve_node below. Real classification behavior is
covered separately in tests/unit/agents/test_triage_agent.py.

This test's job is to confirm the graph wiring itself (including the
conditional retrieve-skip edge) behaves as AGENT-01 specifies, which
unit-testing each node in isolation can't show.
"""

import uuid

import pytest

from regradar.agents import graph as graph_module
from regradar.agents.state import PipelineState
from regradar.models.enums import RiskLevel


def _make_state(risk_level: RiskLevel | None = None) -> PipelineState:
    return PipelineState(filing_id=uuid.uuid4(), raw_text="filing text", risk_level=risk_level)


def _fake_triage_node_setting(risk_level: RiskLevel | None):
    def _fake(state: PipelineState) -> PipelineState:
        return state.model_copy(update={"risk_level": risk_level})

    return _fake


def test_graph_runs_end_to_end_and_reaches_deliver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_module, "triage_node", _fake_triage_node_setting(RiskLevel.HIGH))
    compiled = graph_module.build_graph()
    state = _make_state()

    result = compiled.invoke(state)

    assert result["filing_id"] == state.filing_id
    assert result["risk_level"] == RiskLevel.HIGH


def test_unclassified_filing_takes_the_retrieve_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_module, "triage_node", _fake_triage_node_setting(None))

    calls: list[str] = []
    original_retrieve = graph_module.retrieve_node

    def _spy_retrieve(state: PipelineState) -> PipelineState:
        calls.append("retrieve")
        return original_retrieve(state)

    monkeypatch.setattr(graph_module, "retrieve_node", _spy_retrieve)
    compiled = graph_module.build_graph()

    compiled.invoke(_make_state())

    assert calls == ["retrieve"]


def test_low_risk_filing_skips_the_retrieve_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_module, "triage_node", _fake_triage_node_setting(RiskLevel.LOW))

    calls: list[str] = []
    original_retrieve = graph_module.retrieve_node

    def _spy_retrieve(state: PipelineState) -> PipelineState:
        calls.append("retrieve")
        return original_retrieve(state)

    monkeypatch.setattr(graph_module, "retrieve_node", _spy_retrieve)
    compiled = graph_module.build_graph()

    compiled.invoke(_make_state())

    assert calls == []
