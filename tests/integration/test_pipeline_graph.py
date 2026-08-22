"""End-to-end integration test for the compiled LangGraph pipeline graph.

No real database is needed here — summarize/deliver are stubs operating
purely on in-memory PipelineState, and triage_node/retrieve_node/
analyze_node are all monkeypatched with fakes (each would otherwise
need live infrastructure: triage a live HF API call, retrieve a real DB
session, analyze a live Ollama call) — per this project's
cost/supervision policy, automated tests never call a paid API or need
real infrastructure by default. Real analysis behavior is covered
separately in tests/unit/agents/test_analysis_agent.py.

This test's job is to confirm the graph wiring itself (including the
conditional retrieve-skip edge and the async retrieve node) behaves as
specified, which unit-testing each node in isolation can't show.
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


async def _fake_retrieve_node(state: PipelineState, config) -> PipelineState:
    return state


def _fake_analyze_node(state: PipelineState) -> PipelineState:
    return state


async def test_graph_runs_end_to_end_and_reaches_deliver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_module, "triage_node", _fake_triage_node_setting(RiskLevel.HIGH))
    monkeypatch.setattr(graph_module, "retrieve_node", _fake_retrieve_node)
    monkeypatch.setattr(graph_module, "analyze_node", _fake_analyze_node)
    compiled = graph_module.build_graph()
    state = _make_state()

    result = await compiled.ainvoke(state, config={"configurable": {"db": None}})

    assert result["filing_id"] == state.filing_id
    assert result["risk_level"] == RiskLevel.HIGH


async def test_unclassified_filing_takes_the_retrieve_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_module, "triage_node", _fake_triage_node_setting(None))
    monkeypatch.setattr(graph_module, "analyze_node", _fake_analyze_node)

    calls: list[str] = []

    async def _spy_retrieve(state: PipelineState, config) -> PipelineState:
        calls.append("retrieve")
        return state

    monkeypatch.setattr(graph_module, "retrieve_node", _spy_retrieve)
    compiled = graph_module.build_graph()

    await compiled.ainvoke(_make_state(), config={"configurable": {"db": None}})

    assert calls == ["retrieve"]


async def test_low_risk_filing_skips_the_retrieve_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_module, "triage_node", _fake_triage_node_setting(RiskLevel.LOW))
    monkeypatch.setattr(graph_module, "analyze_node", _fake_analyze_node)

    calls: list[str] = []

    async def _spy_retrieve(state: PipelineState, config) -> PipelineState:
        calls.append("retrieve")
        return state

    monkeypatch.setattr(graph_module, "retrieve_node", _spy_retrieve)
    compiled = graph_module.build_graph()

    await compiled.ainvoke(_make_state(), config={"configurable": {"db": None}})

    assert calls == []
