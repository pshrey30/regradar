"""End-to-end integration test for the compiled LangGraph pipeline graph.

No real database is needed here — analyze/summarize/deliver are stubs
operating purely on in-memory PipelineState, and both triage_node and
retrieve_node are monkeypatched with fakes (triage_node would otherwise
make a live HF API call; retrieve_node would otherwise need a real DB
session) — per this project's cost/supervision policy, automated tests
never call a paid API or need real infrastructure by default. Real
retrieval behavior is covered separately in
tests/unit/rag/test_retriever.py and
tests/unit/agents/test_rag_retrieval_agent.py.

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


async def test_graph_runs_end_to_end_and_reaches_deliver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_module, "triage_node", _fake_triage_node_setting(RiskLevel.HIGH))
    monkeypatch.setattr(graph_module, "retrieve_node", _fake_retrieve_node)
    compiled = graph_module.build_graph()
    state = _make_state()

    result = await compiled.ainvoke(state, config={"configurable": {"db": None}})

    assert result["filing_id"] == state.filing_id
    assert result["risk_level"] == RiskLevel.HIGH


async def test_unclassified_filing_takes_the_retrieve_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_module, "triage_node", _fake_triage_node_setting(None))

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

    calls: list[str] = []

    async def _spy_retrieve(state: PipelineState, config) -> PipelineState:
        calls.append("retrieve")
        return state

    monkeypatch.setattr(graph_module, "retrieve_node", _spy_retrieve)
    compiled = graph_module.build_graph()

    await compiled.ainvoke(_make_state(), config={"configurable": {"db": None}})

    assert calls == []
