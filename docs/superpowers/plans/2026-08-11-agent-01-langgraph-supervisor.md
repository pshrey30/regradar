# AGENT-01 — LangGraph Supervisor Graph & Typed State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared `PipelineState` Pydantic schema and the LangGraph `StateGraph` (with stub
nodes and real conditional routing) that every later Multi-Agent Pipeline ticket plugs into, and wire
ING-06's `process_filing` Celery task to actually invoke it.

**Architecture:** Two new modules in `src/regradar/agents/` (`state.py`, `graph.py`) with zero
dependency on any other agent ticket's real logic — everything except the router is a passthrough
stub. `workers/pipeline_tasks.py` is updated to load a `Filing` row, build an initial `PipelineState`,
and invoke the compiled graph, replacing its current stub log line while keeping ING-06's
retry/failure-marking logic untouched.

**Tech Stack:** `langgraph` (`StateGraph`, `END` — already a pinned dependency, `langgraph>=0.2`,
version 1.2.10 installed in `.venv`), `pydantic` v2, existing `regradar.models.enums.FilingDomain`/
`RiskLevel`.

## Global Constraints

- Design source of truth: `docs/superpowers/specs/2026-08-11-agent-01-langgraph-supervisor-design.md`.
- Python 3.11+, `pytest` with `asyncio_mode = "auto"` (per `pyproject.toml`) — async test functions
  need no `@pytest.mark.asyncio` decorator.
- Reuse `FilingDomain` and `RiskLevel` from `regradar.models.enums` — do not redefine domain/risk
  vocabulary in `agents/state.py`.
- All five graph nodes (`triage_node`, `retrieve_node`, `analyze_node`, `summarize_node`,
  `deliver_node`) are pure passthroughs in this ticket — `return state` unchanged, no logic.
- **Critical LangGraph behavior verified in this environment:** node functions and routing functions
  receive the actual `PipelineState` instance (attribute access works: `state.risk_level`), but
  `compiled_graph.invoke(state)` returns a **plain `dict`**, not a `PipelineState` instance — this is
  LangGraph's standard behavior when the state schema is a Pydantic model. Any code that calls
  `.invoke()` and needs a `PipelineState` back must reconstruct it: `PipelineState(**result)`.
- Follow existing test conventions in `tests/unit/workers/test_pipeline_tasks.py`: env vars are set
  via `os.environ.setdefault(...)` at module top (before other imports) because `celery_app.py`
  resolves `Settings` eagerly at import time; DB calls in unit tests are mocked via
  `monkeypatch.setattr(pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory)`,
  never a real database.

---

### Task 1: `PipelineState` schema

**Files:**
- Create: `src/regradar/agents/state.py`
- Test: `tests/unit/agents/test_state.py`

**Interfaces:**
- Consumes: `regradar.models.enums.FilingDomain`, `regradar.models.enums.RiskLevel` (existing enums).
- Produces (for later tasks in this plan, and for every future AGENT-* ticket):
  - `class RetrievedChunk(BaseModel)`: fields `filing_id: uuid.UUID`, `chunk_text: str`, `score: float`.
  - `class ExtractionResult(BaseModel)`: fields `obligations: list[dict] = []`,
    `deadlines: list[dict] = []`, `risk_flags: list[str] = []`, `affected_products: list[str] = []`,
    `key_entities: list[str] = []`, `competitor_mentions: list[str] = []`,
    `model_used: str | None = None`.
  - `class BriefSet(BaseModel)`: fields `executive_brief: str`, `cco_summary: str`,
    `analyst_summary: str`, `engineer_summary: str`, `model_used: str | None = None`.
  - `class PipelineState(BaseModel)`: fields `filing_id: uuid.UUID`, `raw_text: str`,
    `domain: FilingDomain | None = None`, `risk_level: RiskLevel | None = None`,
    `classification_confidence: float | None = None`,
    `retrieved_chunks: list[RetrievedChunk] | None = None`,
    `extraction: ExtractionResult | None = None`, `briefs: BriefSet | None = None`,
    `delivery_status: str | None = None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/agents/test_state.py`:

```python
"""Unit tests for the shared PipelineState schema."""

import uuid

import pytest
from pydantic import ValidationError

from regradar.agents.state import BriefSet, ExtractionResult, PipelineState, RetrievedChunk
from regradar.models.enums import FilingDomain, RiskLevel


def test_pipeline_state_requires_filing_id_and_raw_text() -> None:
    with pytest.raises(ValidationError):
        PipelineState()  # type: ignore[call-arg]


def test_pipeline_state_minimal_construction_has_none_defaults() -> None:
    filing_id = uuid.uuid4()
    state = PipelineState(filing_id=filing_id, raw_text="some filing text")

    assert state.filing_id == filing_id
    assert state.raw_text == "some filing text"
    assert state.domain is None
    assert state.risk_level is None
    assert state.classification_confidence is None
    assert state.retrieved_chunks is None
    assert state.extraction is None
    assert state.briefs is None
    assert state.delivery_status is None


def test_pipeline_state_accepts_fully_populated_fields() -> None:
    filing_id = uuid.uuid4()
    chunk = RetrievedChunk(filing_id=uuid.uuid4(), chunk_text="matched text", score=0.87)
    extraction = ExtractionResult(
        obligations=[{"description": "file a report", "source_citation": "chunk-3"}],
        deadlines=[{"description": "annual filing", "date": "2026-12-31"}],
        risk_flags=["material weakness"],
        affected_products=["Product X"],
        key_entities=["Acme Corp"],
        competitor_mentions=["Rival Inc"],
        model_used="gpt-4o",
    )
    briefs = BriefSet(
        executive_brief="Acme filed a report flagging a material weakness.",
        cco_summary="High risk: material weakness disclosed.",
        analyst_summary="- File annual report by 2026-12-31",
        engineer_summary="10-K | risk=high | ref: filing/123",
        model_used="gpt-4o",
    )

    state = PipelineState(
        filing_id=filing_id,
        raw_text="full filing text",
        domain=FilingDomain.FINANCIAL,
        risk_level=RiskLevel.HIGH,
        classification_confidence=0.93,
        retrieved_chunks=[chunk],
        extraction=extraction,
        briefs=briefs,
        delivery_status="sent",
    )

    assert state.domain == FilingDomain.FINANCIAL
    assert state.risk_level == RiskLevel.HIGH
    assert state.retrieved_chunks == [chunk]
    assert state.extraction == extraction
    assert state.briefs == briefs
    assert state.delivery_status == "sent"


def test_extraction_result_defaults_to_empty_lists() -> None:
    extraction = ExtractionResult()

    assert extraction.obligations == []
    assert extraction.deadlines == []
    assert extraction.risk_flags == []
    assert extraction.affected_products == []
    assert extraction.key_entities == []
    assert extraction.competitor_mentions == []
    assert extraction.model_used is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/agents/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.agents.state'`

- [ ] **Step 3: Write the implementation**

Create `src/regradar/agents/state.py`:

```python
"""Pydantic state schema shared by every node in the LangGraph pipeline.

Every AGENT-* ticket after this one reads from and writes to
`PipelineState` — it's the one shape that flows all the way from
ingestion hand-off through triage, retrieval, analysis, summarization,
and delivery.
"""

import uuid

from pydantic import BaseModel

from regradar.models.enums import FilingDomain, RiskLevel


class RetrievedChunk(BaseModel):
    """One chunk returned by the RAG retrieval agent (AGENT-06)."""

    filing_id: uuid.UUID
    chunk_text: str
    score: float


class ExtractionResult(BaseModel):
    """Structured output of the Analysis Agent (AGENT-07).

    Field names mirror the `extractions` table's columns 1:1 so this can
    be written to the ORM model via `Extraction(**result.model_dump())`.
    """

    obligations: list[dict] = []
    deadlines: list[dict] = []
    risk_flags: list[str] = []
    affected_products: list[str] = []
    key_entities: list[str] = []
    competitor_mentions: list[str] = []
    model_used: str | None = None


class BriefSet(BaseModel):
    """The four persona briefs produced by the Summarization Agent (AGENT-08).

    Field names mirror the `briefs` table's columns 1:1.
    """

    executive_brief: str
    cco_summary: str
    analyst_summary: str
    engineer_summary: str
    model_used: str | None = None


class PipelineState(BaseModel):
    """Everything that flows through the LangGraph pipeline for one filing."""

    filing_id: uuid.UUID
    raw_text: str
    domain: FilingDomain | None = None
    risk_level: RiskLevel | None = None
    classification_confidence: float | None = None
    retrieved_chunks: list[RetrievedChunk] | None = None
    extraction: ExtractionResult | None = None
    briefs: BriefSet | None = None
    delivery_status: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/agents/test_state.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/agents/state.py tests/unit/agents/test_state.py
git commit -m "Add PipelineState schema for LangGraph pipeline (AGENT-01)"
```

---

### Task 2: Graph nodes, router, and `build_graph`

**Files:**
- Create: `src/regradar/agents/graph.py`
- Test: `tests/unit/agents/test_graph.py`

**Interfaces:**
- Consumes: `regradar.agents.state.PipelineState` (Task 1), `regradar.models.enums.RiskLevel`.
- Produces (for Task 3 and for future AGENT-02/06/07/08/10 tickets to replace):
  - `def triage_node(state: PipelineState) -> PipelineState`
  - `def retrieve_node(state: PipelineState) -> PipelineState`
  - `def analyze_node(state: PipelineState) -> PipelineState`
  - `def summarize_node(state: PipelineState) -> PipelineState`
  - `def deliver_node(state: PipelineState) -> PipelineState`
  - `def route_after_triage(state: PipelineState) -> Literal["retrieve", "analyze"]`
  - `def build_graph() -> CompiledStateGraph` — a freshly compiled graph on every call (no
    module-level singleton), wiring `triage → (route_after_triage) → retrieve|analyze`,
    `retrieve → analyze → summarize → deliver → END`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/agents/test_graph.py`:

```python
"""Unit tests for the LangGraph stub nodes and triage routing decision.

Each node is called directly (not via a compiled graph) so it can be
tested in isolation, per AGENT-01's acceptance criteria.
"""

import uuid

import pytest

from regradar.agents.graph import (
    analyze_node,
    deliver_node,
    retrieve_node,
    route_after_triage,
    summarize_node,
    triage_node,
)
from regradar.agents.state import PipelineState
from regradar.models.enums import RiskLevel


def _make_state(risk_level: RiskLevel | None = None) -> PipelineState:
    return PipelineState(filing_id=uuid.uuid4(), raw_text="filing text", risk_level=risk_level)


@pytest.mark.parametrize(
    "node",
    [triage_node, retrieve_node, analyze_node, summarize_node, deliver_node],
)
def test_stub_node_returns_state_unchanged(node) -> None:
    state = _make_state(risk_level=RiskLevel.HIGH)

    result = node(state)

    assert result == state


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/agents/test_graph.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.agents.graph'`

- [ ] **Step 3: Write the implementation**

Create `src/regradar/agents/graph.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/agents/test_graph.py -v`
Expected: PASS (9 tests: 5 parametrized stub-node cases + 4 routing cases split across the two
parametrized tests — 5 + 3 + 1)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/agents/graph.py tests/unit/agents/test_graph.py
git commit -m "Add LangGraph supervisor graph with stub nodes (AGENT-01)"
```

---

### Task 3: End-to-end integration test for the compiled graph

**Files:**
- Test: `tests/integration/test_pipeline_graph.py`

**Interfaces:**
- Consumes: `regradar.agents.graph.build_graph`, `regradar.agents.graph` module (patched via
  `monkeypatch` to spy on `retrieve_node`), `regradar.agents.state.PipelineState`,
  `regradar.models.enums.RiskLevel`.
- Produces: nothing new — this is a test-only task confirming Task 1 + Task 2 work together as a
  compiled graph, including the conditional branch.

This test needs no real database — the graph and all its nodes operate purely on the in-memory
`PipelineState`, so it belongs in `tests/integration/` only in the sense that it exercises the real
compiled `StateGraph` rather than calling node functions directly (unlike Task 2's unit tests).

**Important:** `compiled_graph.invoke(state)` returns a **plain `dict`**, not a `PipelineState` —
verified directly against the installed `langgraph` 1.2.10 in this repo's `.venv`. Assert against
dict keys (`result["filing_id"]`), or reconstruct with `PipelineState(**result)` if you need
attribute access.

To confirm the conditional edge actually branches (not just that the graph completes), this test
monkeypatches `regradar.agents.graph.retrieve_node` with a wrapper that records whether it ran,
*before* calling `build_graph()` — `build_graph()`'s body looks up `retrieve_node` as a module
global at call time, so patching the module attribute beforehand is picked up by the freshly
compiled graph.

- [ ] **Step 1: Write the test**

Create `tests/integration/test_pipeline_graph.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails first, then passes**

Run: `.venv/bin/pytest tests/integration/test_pipeline_graph.py -v`

This test should be written against the already-implemented Task 1/2 code, so it's expected to
FAIL only if there's a real bug (e.g., the monkeypatch-before-`build_graph()` ordering assumption
turns out wrong in practice). If it fails, verify by adding a `print(graph_module.retrieve_node)`
right before and after the `monkeypatch.setattr` call to confirm the patched function is in place
before `build_graph()` runs.

Expected once correct: PASS (3 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_pipeline_graph.py
git commit -m "Add end-to-end integration test for the pipeline graph (AGENT-01)"
```

---

### Task 4: Wire `process_filing` (ING-06) to invoke the real graph

**Files:**
- Modify: `src/regradar/workers/pipeline_tasks.py`
- Modify: `tests/unit/workers/test_pipeline_tasks.py`

**Interfaces:**
- Consumes: `regradar.agents.graph.build_graph` (Task 2), `regradar.agents.state.PipelineState`
  (Task 1), existing `regradar.core.db.get_session_factory`, `regradar.models.filing.Filing`.
- Produces: `process_filing` (unchanged signature: `process_filing(self, filing_id: str) -> None`,
  a Celery task) now actually runs the pipeline instead of only logging; new private helper
  `async def _run_pipeline_for_filing(filing_id: str) -> None`.

Current `process_filing` body (in `src/regradar/workers/pipeline_tasks.py`):

```python
@celery_app.task(
    base=_ProcessFilingTask,
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def process_filing(self: Task, filing_id: str) -> None:
    """Run the agent pipeline for one filing. Stub — see AGENT-01."""
    logger.info("Processing filing %s (pipeline stub — AGENT-01 not built yet)", filing_id)
```

- [ ] **Step 1: Write the failing test**

In `tests/unit/workers/test_pipeline_tasks.py`, replace the existing
`test_process_filing_stub_runs_without_error` test (it references the old stub-only behavior) with:

```python
def test_process_filing_runs_the_pipeline_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )

    # Calling .run() executes the task body eagerly, in-process.
    process_filing.run(str(filing_id))

    mock_db.get.assert_awaited_once_with(Filing, filing_id)


def test_process_filing_skips_pipeline_when_filing_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=None)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )

    # Should not raise even though the filing doesn't exist.
    process_filing.run(str(uuid.uuid4()))
```

Also add the new import at the top of the test file (alongside the existing `Filing` usage —
`Filing` is not currently imported in this test file, so add it):

```python
from regradar.models.filing import Filing
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/workers/test_pipeline_tasks.py -v`
Expected: FAIL — `mock_db.get.assert_awaited_once_with(...)` fails because the current stub never
calls `db.get`, and/or `ImportError` for `Filing` if not already imported in that test file (it
isn't — check the top-of-file import list before assuming).

- [ ] **Step 3: Write the implementation**

In `src/regradar/workers/pipeline_tasks.py`, add these imports at the top (alongside the existing
ones):

```python
from regradar.agents.graph import build_graph
from regradar.agents.state import PipelineState
```

Add a new async helper function above `process_filing` (after `_mark_filing_failed`, before the
`_ProcessFilingTask` class):

```python
async def _run_pipeline_for_filing(filing_id: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as db:
        filing = await db.get(Filing, uuid.UUID(filing_id))
        if filing is None:
            logger.warning("Filing %s not found — skipping pipeline run", filing_id)
            return

    # Real PDF-to-text extraction doesn't exist yet — AGENT-04's chunking
    # work extracts filing text from the stored S3 PDF. Until then, the
    # pipeline runs against an empty raw_text; every node in AGENT-01 is a
    # stub anyway, so this doesn't block wiring the queue to the graph.
    state = PipelineState(filing_id=filing.id, raw_text="")
    build_graph().invoke(state)
```

Update `process_filing`'s body:

```python
@celery_app.task(
    base=_ProcessFilingTask,
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def process_filing(self: Task, filing_id: str) -> None:
    """Run the agent pipeline for one filing."""
    asyncio.run(_run_pipeline_for_filing(filing_id))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/workers/test_pipeline_tasks.py -v`
Expected: PASS (all tests in the file, including the two new ones and the pre-existing
retry/failure-path tests, which are untouched by this change)

Then run the full test suite touched by this plan to confirm nothing else broke:

Run: `.venv/bin/pytest tests/unit/agents tests/integration/test_pipeline_graph.py tests/unit/workers/test_pipeline_tasks.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/workers/pipeline_tasks.py tests/unit/workers/test_pipeline_tasks.py
git commit -m "Wire process_filing to invoke the LangGraph pipeline (AGENT-01)"
```

---

### Task 5: Full-suite check and push

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: PASS. If any pre-existing test outside this plan's scope fails for unrelated reasons
(e.g., requires a live Postgres/Redis not running locally), confirm it's a pre-existing condition
by checking it also fails on `master` before this branch — do not silently ignore a failure this
branch's changes could plausibly have caused.

- [ ] **Step 2: Run lint and type checks**

Run: `.venv/bin/ruff check src/regradar/agents src/regradar/workers/pipeline_tasks.py tests/unit/agents tests/integration/test_pipeline_graph.py tests/unit/workers/test_pipeline_tasks.py`
Run: `.venv/bin/mypy src/regradar/agents src/regradar/workers/pipeline_tasks.py`
Expected: no errors. Fix any and amend the relevant task's commit-in-progress (a fresh commit is
fine too — don't rewrite already-pushed history).

- [ ] **Step 3: Push the branch**

```bash
git push -u origin agent-01-langgraph-supervisor
```

Do not merge to `master` — per project convention, merging is a separate explicit step the user
confirms.
