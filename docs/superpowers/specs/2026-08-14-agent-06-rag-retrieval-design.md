# AGENT-06 — RAG Retrieval Agent (Hybrid Search + Rerank)

## Context

AGENT-01→05 merged to `master`. `agents/graph.py` has a stub `retrieve_node` wired into the
graph with a conditional edge (`route_after_triage` skips retrieval for low-risk filings).
`filing_chunks` (AGENT-05) has real 768-dim embeddings via local Ollama `nomic-embed-text`.
`PipelineState.retrieved_chunks: list[RetrievedChunk] | None` already exists (AGENT-01;
`RetrievedChunk` has `filing_id`, `chunk_text`, `score`).

## LlamaIndex adoption — concrete shape

The ticket's AI Coding Prompt suggests LlamaIndex 0.10. `llama-index-core` is already a
dependency but unused; using it "properly" surfaced two real constraints during design:

1. **`llama-index-vector-stores-postgres` is not viable here.** It expects to own its own table
   schema (its own column conventions), incompatible with `filing_chunks`'s hand-built schema
   from FOUND-02/AGENT-05. Adopting it would mean migrating away from already-shipped schema —
   not attempted. Instead, dense retrieval is a **custom `BaseRetriever` subclass** that queries
   `filing_chunks.embedding` directly via pgvector cosine distance, using LlamaIndex's retriever
   *interface* without its storage layer.
2. **`QueryFusionRetriever` requires an `llm` even when unused.** It resolves a global LLM at
   construction time (for optional query-expansion), defaulting to OpenAI and failing without a
   key. Fixed by passing a local Ollama LLM (`llama-index-llms-ollama`, `llama3.1` — already
   pulled) and `num_queries=1`, which disables query expansion — verified via a real test that
   this satisfies construction without ever making a network call to Ollama.

New dependencies: `llama-index-retrievers-bm25`, `llama-index-llms-ollama`. No new dependency for
dense retrieval (plain SQLAlchemy/pgvector, already available) or query embedding (reuses
AGENT-05's local-Ollama embedding pattern).

## `src/regradar/rag/retriever.py` (new)

```python
class DenseFilingChunkRetriever(BaseRetriever):
    """Queries filing_chunks.embedding directly via pgvector cosine
    distance — bypasses LlamaIndex's storage layer entirely, since it
    would require a schema incompatible with the existing table."""


async def retrieve_similar_filings(
    query_text: str, exclude_filing_id: UUID, db: AsyncSession, top_k: int = 5
) -> list[RetrievedChunk]:
    """
    1. Pulls all filing_chunks rows (excluding exclude_filing_id) into
       memory, builds a fresh BM25Retriever.from_defaults(nodes=...) per
       call. Known limitation: full-table pull every call — acceptable
       for the current near-empty corpus; revisit if it matters at scale.
    2. Embeds query_text via the local Ollama client (same pattern as
       AGENT-05's _get_embedding_client()).
    3. Runs a pgvector cosine-distance SQL query for dense candidates,
       wrapped in DenseFilingChunkRetriever.
    4. QueryFusionRetriever(mode=FUSION_MODES.RELATIVE_SCORE,
       retriever_weights=[0.5, 0.5], num_queries=1, llm=<local Ollama,
       never invoked over the network>) fuses both ranked lists — this
       IS the "rerank" step: weighted score fusion, no ML cross-encoder.
    5. Walks fused results in rank order, keeping the first chunk per
       distinct filing_id, until top_k distinct filings are collected.
       Empty corpus returns [].
    """
```

New config: `rag_retrieval_top_k: int = Field(default=5, alias="RAG_RETRIEVAL_TOP_K")`.

## Async graph refactor — scoped tightly

Retrieval must run conditionally mid-graph (after triage decides `risk_level`), so it can't be
deferred to a post-graph DB step the way AGENT-02's writes were — it genuinely needs a DB session
inside a graph node. Verified fix: LangGraph's `ainvoke()` runs sync and async nodes together
transparently, and a DB session can be threaded through via `config={"configurable": {"db": db}}`.
Only `retrieve_node` becomes async — `triage_node`/`analyze_node`/`summarize_node`/`deliver_node`
are untouched, verified this mixing works.

`src/regradar/agents/rag_retrieval_agent.py` (new, per the ticket's own file split):

```python
async def retrieve_node(state: PipelineState, config: RunnableConfig) -> PipelineState:
    db = config["configurable"]["db"]
    settings = get_settings()
    chunks = await retrieve_similar_filings(
        state.raw_text, state.filing_id, db, top_k=settings.rag_retrieval_top_k
    )
    return state.model_copy(update={"retrieved_chunks": chunks})
```

`graph.py` imports this real node instead of its stub (same swap pattern as AGENT-02's
`triage_node`). `workers/pipeline_tasks.py` changes `build_graph().invoke(state)` to
`await build_graph().ainvoke(state, config={"configurable": {"db": db}})` — `build_graph()`'s own
signature is unchanged; the DB session is threaded through the `ainvoke()` call, not the graph
construction. Existing tests in `tests/unit/agents/test_graph.py`,
`tests/integration/test_pipeline_graph.py`, and `tests/unit/workers/test_pipeline_tasks.py` update
their `build_graph().invoke(...)` calls to the async pattern — same coverage/intent, updated
mechanics.

## Tests

- Unit tests mock the DB query (`db.execute`/`scalars`); BM25 and fusion run for real in-memory
  (no external call, no mocking needed for LlamaIndex's local components).
- Live verification (briefly, per established policy): real Postgres with a few chunks actually
  embedded via AGENT-05's real pipeline, confirming retrieval returns sensible ranked results
  across distinct filings. Query-text embedding needs a live Ollama call; the fusion/BM25 steps
  themselves do not (per the `num_queries=1` finding above).
