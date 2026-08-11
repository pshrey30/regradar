# AGENT-01 — LangGraph Supervisor Graph & Typed State

## Context

First ticket in the Multi-Agent Pipeline epic, following FOUND-01→05 and ING-01→06 (all merged
to `master`). This is the backbone every later AGENT-* ticket (triage, RAG retrieval, analysis,
summarization, delivery) plugs into: a shared Pydantic state schema and a LangGraph `StateGraph`
wiring stub nodes together with the real conditional routing logic.

`workers/pipeline_tasks.py` (ING-06) already has a `process_filing` Celery task with retry/backoff
and failure-marking logic, but its body is a stub log line — "the real LangGraph pipeline is built
in AGENT-01." This ticket also wires that stub to the real (still-stub-node) graph.

## State schema — `src/regradar/agents/state.py`

Three typed sub-models plus the top-level state:

```python
class RetrievedChunk(BaseModel):
    filing_id: uuid.UUID
    chunk_text: str
    score: float

class ExtractionResult(BaseModel):
    obligations: list[dict] = []       # each: {description, source_citation}
    deadlines: list[dict] = []         # each: {description, date}
    risk_flags: list[str] = []
    affected_products: list[str] = []
    key_entities: list[str] = []
    competitor_mentions: list[str] = []
    model_used: str | None = None

class BriefSet(BaseModel):
    executive_brief: str
    cco_summary: str
    analyst_summary: str
    engineer_summary: str
    model_used: str | None = None

class PipelineState(BaseModel):
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

Design decisions:

- **Reuse existing enums.** `FilingDomain` and `RiskLevel` come from `regradar.models.enums` —
  one source of truth for domain/risk vocabulary shared by the DB schema and the pipeline state,
  rather than a parallel definition that could drift.
- **Typed sub-models for extraction/briefs, not loose dicts.** `ExtractionResult` and `BriefSet`
  field names line up 1:1 with the `extractions`/`briefs` table columns, so AGENT-07/08/10 get
  real validation and can construct ORM rows directly from `result.model_dump()` later, instead of
  discovering the expected shape by reading agent code.
- **`obligations`/`deadlines` stay as `list[dict]`, not further sub-modeled.** AGENT-07 owns
  designing that exact shape (it needs per-obligation source citations per the Security doc's
  hallucination-mitigation requirement) — over-specifying it now would just be replaced.
- **`retrieved_chunks` is typed (`list[RetrievedChunk]`), `delivery_status` stays a loose
  `str | None`.** Retrieval output shape (filing_id/chunk_text/score) is predictable from the RAG
  ticket list; delivery status is a single overall-outcome flag, with per-channel detail already
  owned by the `deliveries` DB table, so a full sub-model would be redundant.

## Graph — `src/regradar/agents/graph.py`

- Five node functions, each `(state: PipelineState) -> PipelineState`, each a **pure passthrough**
  for this ticket (`triage_node`, `retrieve_node`, `analyze_node`, `summarize_node`,
  `deliver_node`) — matches the ticket's literal spec and keeps AGENT-01 scoped to graph wiring,
  not throwaway agent logic. Each is directly callable and testable in isolation without running
  the graph.
- `route_after_triage(state: PipelineState) -> Literal["retrieve", "analyze"]`: returns
  `"analyze"` only when `state.risk_level == RiskLevel.LOW`; every other case (`MEDIUM`, `HIGH`,
  `CRITICAL`, and `None`) returns `"retrieve"`. Since the triage node is still a stub in this
  ticket (AGENT-02 builds the real classifier), `risk_level` will be `None` until then — routing
  `None` to the full retrieve path is the safer default (never silently skip work for a filing
  that hasn't actually been classified yet).
- `build_graph() -> CompiledStateGraph`: `StateGraph(PipelineState)` with edges
  `triage → (conditional: route_after_triage) → retrieve | analyze`, then
  `retrieve → analyze → summarize → deliver → END`.

## Celery integration — `src/regradar/workers/pipeline_tasks.py`

`process_filing(filing_id)` changes from a stub log line to:

1. Load the `Filing` row (as it already does for the failure path).
2. Build `PipelineState(filing_id=filing.id, raw_text="")` — `raw_text` is a placeholder empty
   string for now, with a comment noting real PDF-to-text extraction doesn't exist yet and lands
   with AGENT-04's chunking work. This keeps AGENT-01 scoped to graph wiring, not PDF parsing.
3. Call `build_graph().invoke(state)`.

No change to the existing retry/backoff/failure-marking logic from ING-06 — an exception raised
during `.invoke()` flows through the same `autoretry_for`/`on_failure` machinery already tested in
`tests/unit/workers/test_pipeline_tasks.py`.

## Tests

- `tests/unit/agents/test_graph.py`:
  - Each stub node, called directly with a hand-built `PipelineState`, returns it unchanged.
  - `route_after_triage` returns `"analyze"` for `RiskLevel.LOW`, `"retrieve"` for `MEDIUM`/
    `HIGH`/`CRITICAL`/`None`.
- `tests/integration/test_pipeline_graph.py`:
  - Build the compiled graph, run it end-to-end against a fixture `PipelineState`, assert it
    completes without error and reaches the `deliver` node.
  - One run with `risk_level=None` (takes the `retrieve` path) and one with `risk_level=LOW`
    (skips `retrieve`), confirming the conditional edge actually branches — not just that the
    graph runs.
- `tests/unit/workers/test_pipeline_tasks.py` (existing file, extended not replaced): a new case
  asserting `process_filing` invokes the graph and completes without raising, alongside the
  existing retry/failure-path tests from ING-06.

No live-infrastructure verification is needed for this ticket — there's no external API call or
new DB write beyond loading an existing `Filing` row, so unit + integration tests against the
graph/state logic are the real bar here (the live-verification standard applies to tickets with
actual IO/external-system behavior, which this one doesn't have).
