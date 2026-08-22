# AGENT-06 — RAG Retrieval Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stub `retrieve_node` with a real hybrid BM25 + pgvector retriever, fused via
LlamaIndex's `QueryFusionRetriever`, and thread a DB session into the graph so retrieval can query
`filing_chunks` mid-pipeline.

**Architecture:** `rag/retriever.py` owns the retrieval algorithm (BM25 via LlamaIndex,
dense via a direct pgvector query wrapped in a custom `BaseRetriever`, fused via
`QueryFusionRetriever`). `agents/rag_retrieval_agent.py` owns the one async graph node that calls
it. `graph.py` and `workers/pipeline_tasks.py` switch from `.invoke()` to `.ainvoke()` — verified
this lets sync and async nodes coexist in one graph, and that a DB session can be threaded through
via `config={"configurable": {"db": db}}`.

**Tech Stack:** `llama-index-retrievers-bm25` (BM25 keyword search), `llama-index-llms-ollama`
(satisfies `QueryFusionRetriever`'s required `llm` param without ever calling it over the network,
verified), `pgvector`'s `cosine_distance()` SQLAlchemy operator (dense search, already available).

## Global Constraints

- Design source of truth: `docs/superpowers/specs/2026-08-14-agent-06-rag-retrieval-design.md`.
- Do NOT add `llama-index-vector-stores-postgres` — its schema is incompatible with the existing
  hand-built `filing_chunks` table. Dense retrieval is a custom `BaseRetriever`, not a LlamaIndex
  vector store.
- `QueryFusionRetriever` must be constructed with `llm=Ollama(model=settings.local_llm_model,
  base_url=settings.local_llm_base_url, request_timeout=30.0)` and `num_queries=1` — this
  combination was verified to construct successfully with no live Ollama connection and to never
  make a network call during `.retrieve()` (query expansion is what would call the LLM, and
  `num_queries=1` disables it).
- Fusion mode: `FUSION_MODES.RELATIVE_SCORE`, `retriever_weights=[0.5, 0.5]`, `use_async=False`
  (the fusion call itself runs synchronously inside the async node — no event-loop conflict, since
  it makes no I/O of its own beyond what's already been fetched).
- `retrieve_similar_filings` excludes `exclude_filing_id` from all candidates, deduplicates to one
  chunk per distinct `filing_id` (the first/highest-ranked chunk for that filing), and returns at
  most `top_k` distinct filings.
- Empty corpus (no other filings' chunks exist) returns `[]`, not an error.
- Only `retrieve_node` becomes async. `triage_node`, `analyze_node`, `summarize_node`,
  `deliver_node` are unchanged — verified LangGraph's `ainvoke()` runs sync and async nodes
  together transparently.
- New config: `rag_retrieval_top_k: int = Field(default=5, alias="RAG_RETRIEVAL_TOP_K")`.
- Automated tests never call real Ollama or real Postgres by default. Live verification happens
  explicitly, briefly, per this project's established policy — services started only for the
  check, stopped immediately after.

---

### Task 1: `rag_retrieval_top_k` config

**Files:**
- Modify: `src/regradar/core/config.py`
- Modify: `.env.example`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `Settings.rag_retrieval_top_k: int` (default `5`), for Task 3.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config.py`, inside `test_all_required_fields_present_loads_with_defaults`
(after the existing `assert settings.local_embedding_model == "nomic-embed-text"` line):

```python
    assert settings.rag_retrieval_top_k == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_config.py -v -k all_required_fields`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'rag_retrieval_top_k'`

- [ ] **Step 3: Write the implementation**

In `src/regradar/core/config.py`, add after the existing `chunk_overlap_tokens` field (in the
`# ── RAG chunking ──` section, or as its own line right after it):

```python
    rag_retrieval_top_k: int = Field(default=5, alias="RAG_RETRIEVAL_TOP_K")
```

Add to `.env.example`, after the existing `CHUNK_OVERLAP_TOKENS=50` line:

```
RAG_RETRIEVAL_TOP_K=5
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_config.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/core/config.py .env.example tests/unit/test_config.py
git commit -m "Add rag_retrieval_top_k config (AGENT-06)"
```

---

### Task 2: Add LlamaIndex retrieval dependencies

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `llama_index.retrievers.bm25.BM25Retriever`, `llama_index.llms.ollama.Ollama`,
  `llama_index.core.retrievers.fusion_retriever.QueryFusionRetriever`/`FUSION_MODES` importable,
  for Task 3.

- [ ] **Step 1: Add the dependencies**

In `pyproject.toml`'s `dependencies` list, add these two lines (alongside the existing
`"llama-index>=0.10",` line):

```
    "llama-index-retrievers-bm25>=0.3",
    "llama-index-llms-ollama>=0.5",
```

- [ ] **Step 2: Install and verify**

Run: `.venv/bin/pip install -e .`

Run: `.venv/bin/python3 -c "from llama_index.retrievers.bm25 import BM25Retriever; from llama_index.llms.ollama import Ollama; from llama_index.core.retrievers.fusion_retriever import QueryFusionRetriever, FUSION_MODES; print('ok')"`
Expected: prints `ok`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "Add LlamaIndex BM25/Ollama retrieval dependencies (AGENT-06)"
```

---

### Task 3: `rag/retriever.py` — `DenseFilingChunkRetriever` + `retrieve_similar_filings`

**Files:**
- Create: `src/regradar/rag/retriever.py`
- Create: `tests/unit/rag/test_retriever.py`

**Interfaces:**
- Consumes: `regradar.models.chunk.FilingChunk`, `regradar.agents.state.RetrievedChunk`,
  `regradar.rag.embeddings._get_embedding_client` (AGENT-05, reused for query embedding),
  `Settings.rag_retrieval_top_k`, `Settings.local_llm_model`, `Settings.local_llm_base_url`.
- Produces (for Task 4): `async def retrieve_similar_filings(query_text: str,
  exclude_filing_id: UUID, db: AsyncSession, top_k: int) -> list[RetrievedChunk]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/rag/test_retriever.py`:

```python
"""Unit tests for retrieve_similar_filings. The DB query and the query
embedding call are both mocked — BM25 and fusion run for real in-memory
(no external call needed for either).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from regradar.agents.state import RetrievedChunk
from regradar.rag.retriever import retrieve_similar_filings


def _make_chunk_row(filing_id, chunk_id, text, embedding):
    row = MagicMock()
    row.id = chunk_id
    row.filing_id = filing_id
    row.chunk_text = text
    row.embedding = embedding
    return row


async def test_retrieve_similar_filings_returns_empty_list_for_empty_corpus() -> None:
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await retrieve_similar_filings(
        "some query text", uuid.uuid4(), mock_db, top_k=5
    )

    assert result == []


async def test_retrieve_similar_filings_excludes_current_filing_via_query() -> None:
    exclude_id = uuid.uuid4()
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    await retrieve_similar_filings("query", exclude_id, mock_db, top_k=5)

    mock_db.execute.assert_awaited_once()
    executed_stmt = mock_db.execute.call_args[0][0]
    compiled = str(executed_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert str(exclude_id) in compiled


async def test_retrieve_similar_filings_deduplicates_to_one_chunk_per_filing() -> None:
    filing_a = uuid.uuid4()
    filing_b = uuid.uuid4()
    rows = [
        _make_chunk_row(
            filing_a, uuid.uuid4(), "Material weakness in internal controls disclosed.", [0.1] * 768
        ),
        _make_chunk_row(
            filing_a, uuid.uuid4(), "Routine quarterly filing text.", [0.05] * 768
        ),
        _make_chunk_row(
            filing_b, uuid.uuid4(), "FDA warning letter regarding manufacturing.", [0.2] * 768
        ),
    ]
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rows
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 768)]
    )

    with patch(
        "regradar.rag.retriever._get_embedding_client",
        return_value=(mock_client, "nomic-embed-text"),
    ):
        result = await retrieve_similar_filings(
            "material weakness in internal controls", uuid.uuid4(), mock_db, top_k=5
        )

    result_filing_ids = [r.filing_id for r in result]
    assert len(result_filing_ids) == len(set(result_filing_ids))
    assert filing_a in result_filing_ids
    assert filing_b in result_filing_ids


async def test_retrieve_similar_filings_respects_top_k() -> None:
    rows = [
        _make_chunk_row(uuid.uuid4(), uuid.uuid4(), f"Filing text number {i}.", [0.1 * i] * 768)
        for i in range(10)
    ]
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rows
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 768)]
    )

    with patch(
        "regradar.rag.retriever._get_embedding_client",
        return_value=(mock_client, "nomic-embed-text"),
    ):
        result = await retrieve_similar_filings(
            "filing text", uuid.uuid4(), mock_db, top_k=3
        )

    assert len(result) <= 3


async def test_retrieve_similar_filings_returns_retrieved_chunk_objects() -> None:
    filing_a = uuid.uuid4()
    rows = [
        _make_chunk_row(
            filing_a, uuid.uuid4(), "Material weakness in internal controls disclosed.", [0.1] * 768
        ),
    ]
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rows
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 768)]
    )

    with patch(
        "regradar.rag.retriever._get_embedding_client",
        return_value=(mock_client, "nomic-embed-text"),
    ):
        result = await retrieve_similar_filings(
            "material weakness in internal controls", uuid.uuid4(), mock_db, top_k=5
        )

    assert len(result) == 1
    assert isinstance(result[0], RetrievedChunk)
    assert result[0].filing_id == filing_a
    assert result[0].chunk_text == "Material weakness in internal controls disclosed."
    assert isinstance(result[0].score, float)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/rag/test_retriever.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.rag.retriever'`

- [ ] **Step 3: Write the implementation**

Create `src/regradar/rag/retriever.py`:

```python
"""Hybrid BM25 + pgvector retrieval — finds past filings' chunks similar
to a new filing, fused via LlamaIndex's QueryFusionRetriever.

Dense retrieval is a custom BaseRetriever wrapping a direct pgvector
query against filing_chunks — NOT a LlamaIndex vector store, since those
expect to own their own table schema, incompatible with filing_chunks'
existing hand-built schema (FOUND-02/AGENT-05).
"""

from uuid import UUID

from llama_index.core.retrievers import BaseRetriever
from llama_index.core.retrievers.fusion_retriever import FUSION_MODES, QueryFusionRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from llama_index.llms.ollama import Ollama
from llama_index.retrievers.bm25 import BM25Retriever
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.agents.state import RetrievedChunk
from regradar.core.config import get_settings
from regradar.models.chunk import FilingChunk
from regradar.rag.embeddings import _get_embedding_client

DENSE_CANDIDATE_MULTIPLIER = 4


class DenseFilingChunkRetriever(BaseRetriever):
    """Wraps a pre-fetched, pre-scored list of (node, score) pairs from a
    pgvector cosine-distance query as a LlamaIndex retriever, so it can
    participate in QueryFusionRetriever alongside BM25."""

    def __init__(self, results: list[NodeWithScore]) -> None:
        self._results = results
        super().__init__()

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        return self._results


async def retrieve_similar_filings(
    query_text: str, exclude_filing_id: UUID, db: AsyncSession, top_k: int
) -> list[RetrievedChunk]:
    """Hybrid-retrieve the top_k most similar past filings' chunks.

    1. Embed query_text via the local Ollama client (same pattern as
       rag/embeddings.py's _get_embedding_client()).
    2. Query filing_chunks for the DENSE_CANDIDATE_MULTIPLIER * top_k
       nearest neighbors by cosine distance, excluding exclude_filing_id.
    3. Build a BM25Retriever over the same candidate set's text, and a
       DenseFilingChunkRetriever over their cosine-similarity scores.
    4. Fuse both rankings via QueryFusionRetriever (RELATIVE_SCORE mode,
       equal weights).
    5. Walk fused results in rank order, keeping the first (highest-
       ranked) chunk per distinct filing_id, until top_k distinct
       filings are collected.
    """
    settings = get_settings()
    client, model = _get_embedding_client()
    query_embedding = client.embeddings.create(model=model, input=[query_text]).data[0].embedding

    stmt = (
        select(FilingChunk)
        .where(FilingChunk.filing_id != exclude_filing_id)
        .order_by(FilingChunk.embedding.cosine_distance(query_embedding))
        .limit(top_k * DENSE_CANDIDATE_MULTIPLIER)
    )
    result = await db.execute(stmt)
    candidate_chunks = list(result.scalars().all())

    if not candidate_chunks:
        return []

    nodes = [
        TextNode(
            id_=str(chunk.id),
            text=chunk.chunk_text,
            metadata={"filing_id": str(chunk.filing_id)},
        )
        for chunk in candidate_chunks
    ]
    node_by_id = {node.id_: node for node in nodes}

    dense_results = [
        NodeWithScore(
            node=node,
            score=1.0
            - _cosine_distance(chunk.embedding, query_embedding),
        )
        for node, chunk in zip(nodes, candidate_chunks, strict=True)
    ]
    dense_retriever = DenseFilingChunkRetriever(dense_results)

    bm25_retriever = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=len(nodes))

    local_llm = Ollama(
        model=settings.local_llm_model,
        base_url=settings.local_llm_base_url,
        request_timeout=30.0,
    )
    fusion = QueryFusionRetriever(
        retrievers=[bm25_retriever, dense_retriever],
        llm=local_llm,
        mode=FUSION_MODES.RELATIVE_SCORE,
        retriever_weights=[0.5, 0.5],
        similarity_top_k=len(nodes),
        num_queries=1,
        use_async=False,
    )
    fused = fusion.retrieve(query_text)

    seen_filing_ids: set[str] = set()
    output: list[RetrievedChunk] = []
    for item in fused:
        filing_id_str = item.node.metadata["filing_id"]
        if filing_id_str in seen_filing_ids:
            continue
        seen_filing_ids.add(filing_id_str)
        output.append(
            RetrievedChunk(
                filing_id=UUID(filing_id_str),
                chunk_text=item.node.text,
                score=item.score or 0.0,
            )
        )
        if len(output) >= top_k:
            break

    return output


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 1.0
    return 1.0 - (dot / (norm_a * norm_b))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/rag/test_retriever.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/rag/retriever.py tests/unit/rag/test_retriever.py
git commit -m "Add hybrid BM25 + pgvector retrieval with fusion reranking (AGENT-06)"
```

---

### Task 4: `agents/rag_retrieval_agent.py` — async `retrieve_node`

**Files:**
- Create: `src/regradar/agents/rag_retrieval_agent.py`
- Create: `tests/unit/agents/test_rag_retrieval_agent.py`

**Interfaces:**
- Consumes: `retrieve_similar_filings` (Task 3), `regradar.agents.state.PipelineState`,
  `Settings.rag_retrieval_top_k`.
- Produces (for Task 5): `async def retrieve_node(state: PipelineState, config: RunnableConfig) ->
  PipelineState`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/agents/test_rag_retrieval_agent.py`:

```python
"""Unit tests for the real retrieve_node — retrieve_similar_filings is
mocked; this test only covers the node's own state-wiring logic."""

import uuid
from unittest.mock import AsyncMock, patch

from regradar.agents.rag_retrieval_agent import retrieve_node
from regradar.agents.state import PipelineState, RetrievedChunk


def _make_state() -> PipelineState:
    return PipelineState(filing_id=uuid.uuid4(), raw_text="Filing text about a material weakness.")


async def test_retrieve_node_populates_retrieved_chunks() -> None:
    state = _make_state()
    mock_db = AsyncMock()
    fake_chunks = [
        RetrievedChunk(filing_id=uuid.uuid4(), chunk_text="Similar filing text.", score=0.85)
    ]

    with patch(
        "regradar.agents.rag_retrieval_agent.retrieve_similar_filings",
        AsyncMock(return_value=fake_chunks),
    ) as mock_retrieve:
        result = await retrieve_node(state, {"configurable": {"db": mock_db}})

    mock_retrieve.assert_awaited_once_with(state.raw_text, state.filing_id, mock_db, top_k=5)
    assert result.retrieved_chunks == fake_chunks


async def test_retrieve_node_handles_empty_results() -> None:
    state = _make_state()
    mock_db = AsyncMock()

    with patch(
        "regradar.agents.rag_retrieval_agent.retrieve_similar_filings",
        AsyncMock(return_value=[]),
    ):
        result = await retrieve_node(state, {"configurable": {"db": mock_db}})

    assert result.retrieved_chunks == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/agents/test_rag_retrieval_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.agents.rag_retrieval_agent'`

- [ ] **Step 3: Write the implementation**

Create `src/regradar/agents/rag_retrieval_agent.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/agents/test_rag_retrieval_agent.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/agents/rag_retrieval_agent.py tests/unit/agents/test_rag_retrieval_agent.py
git commit -m "Add async retrieve_node wiring retrieve_similar_filings into the graph (AGENT-06)"
```

---

### Task 5: Wire into `graph.py`, switch to `ainvoke()` in `pipeline_tasks.py`, update existing tests

**Files:**
- Modify: `src/regradar/agents/graph.py`
- Modify: `src/regradar/workers/pipeline_tasks.py`
- Modify: `tests/unit/agents/test_graph.py`
- Modify: `tests/integration/test_pipeline_graph.py`
- Modify: `tests/unit/workers/test_pipeline_tasks.py`

**Interfaces:**
- Consumes: `retrieve_node` (Task 4).
- Produces: `build_graph()`'s compiled graph now runs the real `retrieve_node`; callers use
  `await compiled.ainvoke(state, config={"configurable": {"db": db}})` instead of `.invoke(state)`.

- [ ] **Step 1: Update `graph.py`**

Replace the stub `retrieve_node` import/definition. Current file has a local stub function
definition — remove it and import the real one instead, matching the pattern already used for
`triage_node` (AGENT-02):

```python
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
```

- [ ] **Step 2: Update `pipeline_tasks.py`**

In `src/regradar/workers/pipeline_tasks.py`, change the line:

```python
        result = build_graph().invoke(state)
```

to:

```python
        result = await build_graph().ainvoke(state, config={"configurable": {"db": db}})
```

No other changes needed in this file — `_run_pipeline_for_filing` is already `async def` and runs
inside `db`'s open session, so `db` is in scope at this point.

- [ ] **Step 3: Update `tests/unit/agents/test_graph.py`**

`retrieve_node` is no longer imported from `graph` (it now comes from `rag_retrieval_agent`) and
is no longer a plain stub, so it drops out of this file's stub-passthrough test entirely (its
behavior is covered in `tests/unit/agents/test_rag_retrieval_agent.py`). Replace the file's
imports and parametrized test:

```python
from regradar.agents.graph import (
    analyze_node,
    deliver_node,
    route_after_triage,
    summarize_node,
)
```

```python
@pytest.mark.parametrize(
    "node",
    [analyze_node, summarize_node, deliver_node],
)
def test_stub_node_returns_state_unchanged(node) -> None:
    state = _make_state(risk_level=RiskLevel.HIGH)

    result = node(state)

    assert result == state
```

(everything else in the file — `_make_state`, the `route_after_triage` tests — is unchanged)

- [ ] **Step 4: Update `tests/integration/test_pipeline_graph.py`**

The existing tests monkeypatch `graph_module.retrieve_node` with a sync fake and call
`compiled.invoke(...)`. Update to the async pattern — `graph_module.retrieve_node` must now be an
async function (since `graph.py` imports the real async one, and LangGraph nodes registered as
async are invoked with `await`), and the graph is run via `ainvoke`. Replace the whole file:

```python
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
```

- [ ] **Step 5: Update `tests/unit/workers/test_pipeline_tasks.py`**

Five places mock `build_graph` with `lambda: MagicMock(invoke=lambda state: {...})` or
`lambda: MagicMock(invoke=_fake_invoke)`. Each needs `invoke=` changed to `ainvoke=AsyncMock(return_value=...)` (for the two static-return-value cases) or the custom function converted to `async def` with the mock attribute renamed. Add `AsyncMock` to imports if not already present (it already is, per the existing `from unittest.mock import AsyncMock, MagicMock, patch` line).

In `test_process_filing_persists_classification_on_success`, change:

```python
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            invoke=lambda state: {
                "domain": FilingDomain.FINANCIAL,
                "risk_level": RiskLevel.LOW,
                "classification_confidence": 0.9,
            }
        ),
    )
```

to:

```python
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.FINANCIAL,
                    "risk_level": RiskLevel.LOW,
                    "classification_confidence": 0.9,
                }
            )
        ),
    )
```

Apply the identical transformation (dict literal unchanged, just `invoke=lambda state: {...}` →
`ainvoke=AsyncMock(return_value={...})`) in:
- `test_process_filing_marks_needs_classification_when_triage_fails` (the `domain`/`risk_level`/
  `classification_confidence` all `None` dict)
- `test_process_filing_extracts_text_and_embeds_chunks_when_pdf_present` (the `FINANCIAL`/`LOW`/
  `0.9` dict)
- `test_process_filing_skips_extraction_when_no_pdf_key` (the `FINANCIAL`/`LOW`/`0.9` dict)

For `test_process_filing_falls_back_to_empty_text_when_pdf_extraction_fails`, which uses a custom
`_fake_invoke` function to capture the state passed in, change:

```python
    captured_state = {}

    def _fake_invoke(state):
        captured_state["raw_text"] = state.raw_text
        return {
            "domain": FilingDomain.FINANCIAL,
            "risk_level": RiskLevel.LOW,
            "classification_confidence": 0.9,
        }

    monkeypatch.setattr(
        pipeline_tasks_module, "build_graph", lambda: MagicMock(invoke=_fake_invoke)
    )
```

to:

```python
    captured_state = {}

    async def _fake_ainvoke(state, config=None):
        captured_state["raw_text"] = state.raw_text
        return {
            "domain": FilingDomain.FINANCIAL,
            "risk_level": RiskLevel.LOW,
            "classification_confidence": 0.9,
        }

    monkeypatch.setattr(
        pipeline_tasks_module, "build_graph", lambda: MagicMock(ainvoke=_fake_ainvoke)
    )
```

- [ ] **Step 6: Run all affected tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/agents/test_graph.py tests/integration/test_pipeline_graph.py tests/unit/workers/test_pipeline_tasks.py tests/unit/agents/test_rag_retrieval_agent.py -v`
Expected: PASS (every test in these four files)

- [ ] **Step 7: Commit**

```bash
git add src/regradar/agents/graph.py src/regradar/workers/pipeline_tasks.py tests/unit/agents/test_graph.py tests/integration/test_pipeline_graph.py tests/unit/workers/test_pipeline_tasks.py
git commit -m "Wire real retrieve_node into the graph; switch to async ainvoke (AGENT-06)"
```

---

### Task 6: Live verification, full-suite check, lint, mypy, push

**Files:** none (verification only)

- [ ] **Step 1: Live-verify retrieval against real Postgres + Ollama**

Start both briefly:

```bash
docker compose -f infra/docker-compose.yml up -d postgres
/opt/homebrew/opt/ollama/bin/ollama serve > /tmp/ollama-serve.log 2>&1 &
```

Wait for both to be ready (`docker exec infra-postgres-1 pg_isready -U regradar`, `curl -s
http://localhost:11434`). Confirm `nomic-embed-text` and `llama3.1` are still pulled:
`/opt/homebrew/opt/ollama/bin/ollama list`.

Run `export DATABASE_URL=$(grep '^DATABASE_URL=' .env | cut -d= -f2-) && .venv/bin/alembic upgrade
head` to ensure the schema is current.

Write and run a quick interactive script (adjust as needed): insert two or three `Filing` rows
with a few `FilingChunk` rows each (distinct `filing_id`s, real text, embeddings computed via the
same local Ollama client `rag/embeddings.py` uses — reuse `_get_embedding_client()` directly for
this), then call `retrieve_similar_filings(query_text, exclude_filing_id, db, top_k=5)` for a
query related to one of the inserted filings' content, and confirm the results rank the topically
related filing above the unrelated one(s), excluding `exclude_filing_id` from the results. Clean
up the inserted rows afterward.

Expected: no errors; retrieval returns sensible, correctly-excluded, ranked results.

- [ ] **Step 2: Stop both services**

```bash
pkill -f "ollama serve"
docker compose -f infra/docker-compose.yml stop postgres
```

Confirm both are down: `curl -s http://localhost:11434` should fail to connect; `docker ps` should
show no running containers.

- [ ] **Step 3: Run the full default test suite**

Run: `.venv/bin/pytest -v --ignore=tests/integration/test_flows.py`
Expected: PASS (all tests; `test_flows.py` needs a live Postgres this ticket's default test run
doesn't start, consistent with prior tickets).

- [ ] **Step 4: Run lint and type checks**

Run: `.venv/bin/ruff check src/regradar/rag src/regradar/agents src/regradar/core/config.py src/regradar/workers/pipeline_tasks.py tests/unit/rag tests/unit/agents tests/integration/test_pipeline_graph.py tests/unit/workers/test_pipeline_tasks.py tests/unit/test_config.py`
Run: `.venv/bin/mypy src/regradar/rag src/regradar/agents src/regradar/core/config.py src/regradar/workers/pipeline_tasks.py`
Expected: no errors. Fix any and commit the fix as part of this task.

- [ ] **Step 5: Push the branch**

```bash
git push -u origin agent-06-rag-retrieval
```

Do not merge to `master` — merging is a separate explicit step the user confirms.
