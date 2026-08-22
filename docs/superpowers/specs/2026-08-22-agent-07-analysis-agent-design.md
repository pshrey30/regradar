# AGENT-07 — Analysis Agent (Structured Extraction)

## Context

AGENT-01→06 merged to `master`. `agents/graph.py` has a stub `analyze_node`. `PipelineState`
already has `extraction: ExtractionResult | None` (AGENT-01) and `retrieved_chunks:
list[RetrievedChunk] | None` (AGENT-06, populated only when the graph took the retrieve path).
The `extractions` DB table (FOUND-02) already exists with matching columns; nothing INSERTs into
it yet.

## Model: local Ollama, continuing the established pattern

Real OpenAI/Anthropic credits remain unavailable (established during AGENT-03). Live-verified
during design: `llama3.1` via Ollama, called with `response_format={"type": "json_schema",
"strict": True}`, produces valid JSON matching a real extraction schema, with source citations
that closely match the source text (one observed case had a capitalization difference at a
sentence boundary — the model normalizing a mid-sentence clause into a standalone quote, not a
hallucination). This confirms local `llama3.1` is workable for structured extraction; if quality
proves insufficient once EVAL-04's real F1 harness exists, switching providers is a config change
(`USE_LOCAL_LLM`), not a rewrite.

## Citation format: real `chunk_index`, not raw-text quotes

The ticket's literal wording ("reference back to the specific chunk/section") presumes chunk
identity exists at extraction time — it doesn't currently, since `chunk_filing()` runs *after*
the graph in `process_filing`. Verbatim-quote citations (self-contained, fuzzy-matched against
`raw_text`) were considered but rejected: duplicate/boilerplate sentences make substring matching
ambiguous, and it would be the only citation scheme in the codebase not based on real chunk
identity (AGENT-06's retrieval already cites past filings by real chunk data). Chosen instead:
**restructure `process_filing` so `chunk_filing()` runs right after PDF extraction, before the
graph**, giving `analyze_node` real, stable `chunk_index` values to cite. This is a small reorder,
not a rewrite — `chunk_filing()` is already a pure, cheap, DB-free function; only the expensive
step (embedding) stays deferred to after the graph, and reuses the same already-computed chunk
list rather than recomputing it.

`src/regradar/workers/pipeline_tasks.py`'s `_run_pipeline_for_filing`:

```python
raw_text, tables = extract_text_and_tables(pdf_bytes)  # unchanged
chunks = chunk_filing(raw_text, tables) if raw_text else []  # moved earlier
state = PipelineState(filing_id=filing.id, raw_text=raw_text, chunks=chunks)
result = await build_graph().ainvoke(state, config={"configurable": {"db": db}})
# ...existing domain/risk_level persistence...
if raw_text:
    await embed_chunks(filing.id, chunks, db)  # reuses `chunks`, no recompute
```

`PipelineState` (`agents/state.py`) gains `chunks: list[Chunk] | None = None`.

## Node placement: sync/pure, matching `triage_node`

`analyze_node` doesn't need mid-graph I/O — everything it needs (`state.chunks`,
`state.retrieved_chunks`) is already in `PipelineState` once chunking moved earlier. Async is
reserved for nodes that structurally require mid-graph I/O (AGENT-06's `retrieve_node`); applying
it elsewhere would spread `AsyncMock`/event-loop complexity through tests with no functional
benefit, and would blur the signal that "async node" currently and reliably means "needs
mid-graph I/O." `analyze_node` stays a plain sync function; the `extractions`-table INSERT is
deferred to `process_filing`, mirroring exactly how `triage_node`'s domain/risk_level are
persisted after the graph completes.

## `src/regradar/agents/analysis_agent.py` (new)

```python
class AnalysisError(Exception):
    """Raised internally when extraction fails after retry — caught by
    analyze_node, never propagates out of it."""


def _get_llm_client() -> tuple[OpenAI, str]:
    """Same local-Ollama/real-OpenAI routing as triage_agent.py's and
    embeddings.py's helpers of the same name — a small, module-local
    copy rather than a shared import, matching this codebase's existing
    per-module duplication of this exact helper."""


def _build_extraction_prompt(state: PipelineState) -> str:
    """Lists each state.chunks entry as "[chunk N]: <chunk_text>", plus
    retrieved_chunks context (if present) as grounding, per the ticket's
    "include retrieved similar-filings context for grounding"
    requirement."""


def _call_extraction_model(prompt: str, strict: bool) -> dict:
    """One call to the local/real model with response_format=json_schema
    (strict=True), schema requiring every obligation to include
    source_chunk_index: int (not source_citation: str — a chunk index,
    not a text quote). strict=True on retry adds an explicit
    "the previous response was malformed; every field is required, every
    obligation MUST include a valid source_chunk_index" instruction."""


def analyze_node(state: PipelineState) -> PipelineState:
    """Builds the prompt, calls the model, validates the response
    (schema fields present, every obligation's source_chunk_index is a
    real index into state.chunks). On success, returns state with
    extraction populated. On failure (malformed JSON, schema violation,
    or an invalid chunk index) retries once with a stricter prompt. If
    the retry also fails, returns state unchanged (extraction stays
    None) — process_filing reads this as the signal to mark the filing
    needs_review, the same None-signals-failure pattern triage_node
    established for needs_classification.
    """
```

`ExtractionResult.obligations` (`agents/state.py`) keeps its existing `list[dict]` type (per
AGENT-01's original scoping decision — this ticket defines the dict shape it will actually
contain, `{"description": str, "source_chunk_index": int}`, without changing the field's type).

## `process_filing` persistence

After the graph, mirroring the existing domain/risk_level pattern:

```python
if result["extraction"] is None:
    filing.status = FilingStatus.NEEDS_REVIEW
else:
    db.add(Extraction(
        filing_id=filing.id,
        obligations=result["extraction"]["obligations"],
        deadlines=result["extraction"]["deadlines"],
        risk_flags=result["extraction"]["risk_flags"],
        affected_products=result["extraction"]["affected_products"],
        key_entities=result["extraction"]["key_entities"],
        competitor_mentions=result["extraction"]["competitor_mentions"],
        model_used=result["extraction"]["model_used"],
        raw_model_response=<the raw parsed JSON from the successful call>,
    ))
```

(Exact field-by-field construction rather than `**result["extraction"]` since `Extraction`'s
`raw_model_response` isn't a field on `ExtractionResult` and needs separate plumbing — resolved
precisely in the implementation plan.)

## Migration 0006 — `FilingStatus.NEEDS_REVIEW`

Identical pattern to migration 0004 (`NEEDS_CLASSIFICATION`): `ALTER TYPE filing_status ADD VALUE`
inside `autocommit_block()` for upgrade; downgrade brackets the enum-type-swap with `ALTER TABLE
... ALTER COLUMN status DROP DEFAULT` / `... SET DEFAULT 'ingested'` (the fix discovered during
AGENT-02's migration 0004, verified working).

## Tests

- Unit tests mock the Ollama client entirely: schema-valid response with valid chunk indices;
  malformed-then-retry-succeeds; malformed-twice (extraction stays `None`); a response with an
  obligation citing an out-of-range `source_chunk_index` (treated as a validation failure,
  triggers the same retry-then-`None` path).
- Live verification (briefly, per established policy): a real Ollama call against real fixture
  text confirming schema-conformant output with valid chunk indices; real Postgres for the
  migration 0006 upgrade/downgrade/upgrade cycle.
