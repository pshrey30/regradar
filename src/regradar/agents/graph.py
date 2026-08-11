"""The LangGraph supervisor graph wiring the six pipeline agents together.

Every node function here is a stub for AGENT-01 — each later ticket
(AGENT-02, AGENT-06, AGENT-07, AGENT-08, AGENT-10) replaces exactly one
node's body with real behavior. The graph wiring and the triage routing
decision are the real, permanent parts of this module.
"""

from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from regradar.agents.state import PipelineState
from regradar.models.enums import RiskLevel


def triage_node(state: PipelineState) -> PipelineState:
    """Stub — replaced by the real zero-shot classifier in AGENT-02."""
    return state


def retrieve_node(state: PipelineState) -> PipelineState:
    """Stub — replaced by the hybrid BM25 + vector retriever in AGENT-06."""
    return state


def analyze_node(state: PipelineState) -> PipelineState:
    """Stub — replaced by the structured-extraction agent in AGENT-07."""
    return state


def summarize_node(state: PipelineState) -> PipelineState:
    """Stub — replaced by the persona-brief generator in AGENT-08."""
    return state


def deliver_node(state: PipelineState) -> PipelineState:
    """Stub — replaced by the Slack/email/webhook fan-out agent in AGENT-10."""
    return state


def route_after_triage(state: PipelineState) -> Literal["retrieve", "analyze"]:
    """Decide whether a filing needs the deep RAG retrieval step.

    Low-risk filings skip straight to analysis. An unclassified filing
    (risk_level is None — the case until AGENT-02 replaces the triage
    stub) is treated the same as any non-low risk level: it gets the
    full retrieve step, since skipping work for a filing we haven't
    actually classified yet is the wrong default.
    """
    if state.risk_level == RiskLevel.LOW:
        return "analyze"
    return "retrieve"


def build_graph() -> CompiledStateGraph:
    """Compile the pipeline graph. Called fresh each time — no caching."""
    graph = StateGraph(PipelineState)

    graph.add_node("triage", triage_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("deliver", deliver_node)

    graph.set_entry_point("triage")
    graph.add_conditional_edges(
        "triage", route_after_triage, {"retrieve": "retrieve", "analyze": "analyze"}
    )
    graph.add_edge("retrieve", "analyze")
    graph.add_edge("analyze", "summarize")
    graph.add_edge("summarize", "deliver")
    graph.add_edge("deliver", END)

    return graph.compile()
