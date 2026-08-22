"""The real retrieve graph node — replaces AGENT-01's passthrough stub.

Retrieval is a read that must happen mid-graph (after triage decides
risk_level, before analyze), so unlike AGENT-02's writes it cannot be
deferred to a post-graph step in process_filing. This node is async and
receives its DB session via LangGraph's config={"configurable": {"db":
db}} mechanism — the only async node in the graph; every other node
stays a plain sync function.
"""

from langchain_core.runnables import RunnableConfig

from regradar.agents.state import PipelineState
from regradar.core.config import get_settings
from regradar.rag.retriever import retrieve_similar_filings


async def retrieve_node(state: PipelineState, config: RunnableConfig) -> PipelineState:
    db = config["configurable"]["db"]
    settings = get_settings()
    chunks = await retrieve_similar_filings(
        state.raw_text, state.filing_id, db, top_k=settings.rag_retrieval_top_k
    )
    return state.model_copy(update={"retrieved_chunks": chunks})
