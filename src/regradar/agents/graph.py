"""The LangGraph supervisor graph wiring the six pipeline agents together.

triage_node and retrieve_node are real implementations (AGENT-02,
AGENT-06) — analyze_node/summarize_node/deliver_node are still stubs for
later tickets. retrieve_node is the only async node; the graph is run
via ainvoke() (not invoke()) so it can await retrieve_node's DB query —
LangGraph mixes sync and async nodes transparently in async execution.
"""

from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from regradar.agents.rag_retrieval_agent import retrieve_node
from regradar.agents.state import PipelineState
from regradar.agents.triage_agent import triage_node
from regradar.models.enums import RiskLevel


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
    """Compile the pipeline graph. Called fresh each time — no caching.

    Run via ainvoke(state, config={"configurable": {"db": db}}) — the
    retrieve node needs a DB session passed through this mechanism.
    """
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
