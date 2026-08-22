# AGENT-07 — Analysis Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stub `analyze_node` with a real structured-extraction agent that calls local
Ollama (`llama3.1`) with a strict JSON schema, citing real `chunk_index` values instead of raw-text
quotes — requiring `chunk_filing()` to run before the graph instead of after.

**Architecture:** `agents/analysis_agent.py` owns the extraction call and validation, as a plain
sync function (`analyze_node`) matching `triage_node`'s pattern — no DB access, output lands in
`PipelineState.extraction`. `workers/pipeline_tasks.py` reorders so chunking happens before
`PipelineState` construction (giving `analyze_node` real chunks to cite) and persists the
extraction result after the graph, mirroring how domain/risk_level are already persisted.

**Tech Stack:** `openai` SDK pointed at local Ollama (existing pattern from AGENT-03/05/06),
`response_format={"type": "json_schema", "strict": True}` — verified live during design to
produce valid, schema-conformant JSON from `llama3.1`.

## Global Constraints

- Design source of truth: `docs/superpowers/specs/2026-08-22-agent-07-analysis-agent-design.md`.
- `analyze_node` is a plain sync function — no DB access, no `async`. It receives everything it
  needs from `PipelineState` (`raw_text`, `chunks`, `retrieved_chunks`).
- Citations are `source_chunk_index: int` (a real index into `state.chunks`), never raw-text
  quotes.
- `chunk_filing()` moves in `workers/pipeline_tasks.py` from after the graph to right after PDF
  extraction, before `PipelineState` is constructed. The same computed `chunks` list is reused for
  both the graph invocation and the later `embed_chunks()` call — never recomputed.
- **Verified via a real LangGraph call**: `ainvoke()`'s returned dict has the top-level
  `PipelineState` fields as plain dict keys (per AGENT-01), but a nested Pydantic sub-model value
  (like `extraction: ExtractionResult | None`) stays as the actual `ExtractionResult` *instance* —
  it is NOT flattened into a plain dict. `result["extraction"]` in `pipeline_tasks.py` must
  therefore use **attribute access** (`result["extraction"].obligations`, `.model_dump()` for
  `raw_model_response`), never dict-subscript access (`result["extraction"]["obligations"]`) —
  the latter would raise `TypeError`. Test mocks in Task 5 return real `ExtractionResult`
  instances for this key, not plain dicts, to match production behavior exactly.
- On extraction failure (malformed JSON, missing schema fields, or any obligation citing an
  out-of-range `source_chunk_index`): retry once with a stricter prompt. If the retry also fails,
  `analyze_node` returns `state` with `extraction` unchanged (`None`) — `process_filing` reads
  this as the signal to set `filing.status = FilingStatus.NEEDS_REVIEW`.
- `FilingStatus.NEEDS_REVIEW` migration (0006) follows migration 0004's exact pattern, including
  the downgrade's `DROP DEFAULT`/`SET DEFAULT` bracketing around the enum-type swap.
- No circular import: `agents/state.py` importing `Chunk` from `regradar.rag.chunking` is safe —
  `rag/chunking.py` has no import of `agents/state.py` (verified: its only local import is
  `regradar.core.config`).
- Automated tests never call real Ollama or real Postgres by default. Live verification happens
  explicitly, briefly, per this project's established policy — services started only for the
  check, stopped immediately after.

---

### Task 1: `PipelineState.chunks` field + `FilingStatus.NEEDS_REVIEW` enum value

**Files:**
- Modify: `src/regradar/agents/state.py`
- Modify: `src/regradar/models/enums.py`
- Test: `tests/unit/agents/test_state.py`

**Interfaces:**
- Produces: `PipelineState.chunks: list[Chunk] | None = None` (for Task 4), `FilingStatus.
  NEEDS_REVIEW` (value `"needs_review"`, for Task 2 and Task 5).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/agents/test_state.py` (check the existing file for its exact import list and
test-naming conventions first; add a new test rather than replacing anything):

```python
def test_pipeline_state_accepts_chunks_field() -> None:
    from regradar.rag.chunking import Chunk

    chunk = Chunk(
        chunk_index=0,
        chunk_text="Some filing text.",
        section_reference=None,
        token_count=4,
        is_table=False,
    )
    state = PipelineState(filing_id=uuid.uuid4(), raw_text="Some filing text.", chunks=[chunk])

    assert state.chunks == [chunk]


def test_pipeline_state_chunks_defaults_to_none() -> None:
    state = PipelineState(filing_id=uuid.uuid4(), raw_text="text")

    assert state.chunks is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/agents/test_state.py -v -k chunks`
Expected: FAIL with `pydantic.ValidationError` (unexpected keyword argument `chunks`) or
`AttributeError` on `state.chunks`.

- [ ] **Step 3: Write the implementation**

In `src/regradar/agents/state.py`, add the import and field:

```python
from regradar.rag.chunking import Chunk
```

(add alongside the existing `from regradar.models.enums import FilingDomain, RiskLevel` line)

```python
class PipelineState(BaseModel):
    """Everything that flows through the LangGraph pipeline for one filing."""

    filing_id: uuid.UUID
    raw_text: str
    domain: FilingDomain | None = None
    risk_level: RiskLevel | None = None
    classification_confidence: float | None = None
    retrieved_chunks: list[RetrievedChunk] | None = None
    chunks: list[Chunk] | None = None
    extraction: ExtractionResult | None = None
    briefs: BriefSet | None = None
    delivery_status: str | None = None
```

(insert `chunks: list[Chunk] | None = None` — position among the fields doesn't matter
functionally; placing it near `retrieved_chunks` keeps related fields together)

In `src/regradar/models/enums.py`, add to `FilingStatus`:

```python
class FilingStatus(str, enum.Enum):
    INGESTED = "ingested"
    CLASSIFYING = "classifying"
    NEEDS_CLASSIFICATION = "needs_classification"
    NEEDS_REVIEW = "needs_review"
    RETRIEVING = "retrieving"
    ANALYZING = "analyzing"
    SUMMARIZING = "summarizing"
    DELIVERING = "delivering"
    COMPLETE = "complete"
    FAILED = "failed"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/agents/test_state.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/agents/state.py src/regradar/models/enums.py tests/unit/agents/test_state.py
git commit -m "Add PipelineState.chunks field and FilingStatus.NEEDS_REVIEW (AGENT-07)"
```

---

### Task 2: Migration 0006 — `FilingStatus.NEEDS_REVIEW`

**Files:**
- Create: `migrations/versions/0006_add_needs_review_status.py`

**Interfaces:**
- Consumes: `FilingStatus.NEEDS_REVIEW` (Task 1).
- Produces: nothing consumed by later tasks — verified independently against real Postgres.

- [ ] **Step 1: Write the migration**

Create `migrations/versions/0006_add_needs_review_status.py`:

```python
"""Add needs_review to the filing_status enum.

AGENT-07's Analysis Agent sets this status when structured extraction
fails validation twice (malformed JSON, missing schema fields, or an
out-of-range source_chunk_index), instead of saving incomplete data.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-22
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

NEW_VALUE = "needs_review"
ORIGINAL_VALUES = [
    "ingested",
    "classifying",
    "needs_classification",
    "retrieving",
    "analyzing",
    "summarizing",
    "delivering",
    "complete",
    "failed",
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE filing_status ADD VALUE '{NEW_VALUE}'")


def downgrade() -> None:
    op.execute(f"UPDATE filings SET status = 'failed' WHERE status = '{NEW_VALUE}'")
    values_sql = ", ".join(f"'{v}'" for v in ORIGINAL_VALUES)
    op.execute(f"CREATE TYPE filing_status_old AS ENUM ({values_sql})")
    op.execute("ALTER TABLE filings ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE filings ALTER COLUMN status TYPE filing_status_old "
        "USING status::text::filing_status_old"
    )
    op.execute("DROP TYPE filing_status")
    op.execute("ALTER TYPE filing_status_old RENAME TO filing_status")
    op.execute("ALTER TABLE filings ALTER COLUMN status SET DEFAULT 'ingested'")
```

- [ ] **Step 2: Run the migration against a real Postgres to verify it works**

Start Postgres briefly: `docker compose -f infra/docker-compose.yml up -d postgres`, wait for
health (`docker exec infra-postgres-1 pg_isready -U regradar`).

```bash
export DATABASE_URL=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)
.venv/bin/alembic upgrade head
```

Expected: migration `0006` applies cleanly on top of `0005`.

Run: `.venv/bin/alembic downgrade -1`
Expected: no errors; `filing_status` enum reverts to its original 9 values (the 8 from before
migration 0004, plus `needs_classification` from 0004 itself — 0006's downgrade only removes
`needs_review`).

Run: `.venv/bin/alembic upgrade head` again to leave the DB at head, then stop Postgres:
`docker compose -f infra/docker-compose.yml stop postgres`. Do not leave it running.

- [ ] **Step 3: Commit**

```bash
git add migrations/versions/0006_add_needs_review_status.py
git commit -m "Add needs_review filing status for failed extraction (AGENT-07)"
```

---

### Task 3: `analysis_agent.py` — extraction call + schema validation

**Files:**
- Create: `src/regradar/agents/analysis_agent.py`
- Create: `tests/unit/agents/test_analysis_agent.py`

**Interfaces:**
- Consumes: `regradar.agents.state.PipelineState`, `regradar.agents.state.ExtractionResult`,
  `regradar.core.config.get_settings` (`use_local_llm`, `local_llm_base_url`, `local_llm_model`,
  `openai_api_key`, `tier_high_model`).
- Produces: `class AnalysisError(Exception)`, `def analyze_node(state: PipelineState) ->
  PipelineState`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/agents/test_analysis_agent.py`:

```python
"""Unit tests for the Analysis Agent's structured extraction call.

The OpenAI-compatible client is always mocked — no real Ollama or
OpenAI call in these tests. See test_analysis_agent_live_smoke.py (this
same file, marked @pytest.mark.live) for the one test allowed to hit the
real local Ollama server.
"""

import json
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest

from regradar.agents.analysis_agent import analyze_node
from regradar.agents.state import PipelineState
from regradar.rag.chunking import Chunk

VALID_EXTRACTION_JSON = {
    "obligations": [
        {
            "description": "File annual compliance certification by January 15, 2027.",
            "source_chunk_index": 0,
        }
    ],
    "deadlines": [{"description": "Annual compliance certification", "date": "2027-01-15"}],
    "risk_flags": ["material weakness"],
    "affected_products": ["Product X"],
    "key_entities": ["Acme Corp"],
    "competitor_mentions": [],
}


def _mock_openai_client(content: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    client.chat.completions.create.return_value = response
    return client


def _make_state_with_chunks(chunk_count: int = 1) -> PipelineState:
    chunks = [
        Chunk(
            chunk_index=i,
            chunk_text=f"Chunk {i} text about compliance obligations.",
            section_reference=None,
            token_count=6,
            is_table=False,
        )
        for i in range(chunk_count)
    ]
    return PipelineState(
        filing_id=uuid.uuid4(), raw_text="Full filing text.", chunks=chunks
    )


def test_analyze_node_populates_extraction_on_valid_response() -> None:
    content = json.dumps(VALID_EXTRACTION_JSON)
    with patch(
        "regradar.agents.analysis_agent._get_llm_client",
        return_value=(_mock_openai_client(content), "llama3.1"),
    ):
        result = analyze_node(_make_state_with_chunks())

    assert result.extraction is not None
    assert result.extraction.obligations == VALID_EXTRACTION_JSON["obligations"]
    assert result.extraction.deadlines == VALID_EXTRACTION_JSON["deadlines"]
    assert result.extraction.risk_flags == ["material weakness"]
    assert result.extraction.model_used == "llama3.1"


def test_analyze_node_retries_once_on_malformed_json_then_succeeds() -> None:
    valid_content = json.dumps(VALID_EXTRACTION_JSON)
    client = MagicMock()
    malformed_response = MagicMock()
    malformed_response.choices = [MagicMock(message=MagicMock(content="not valid json"))]
    valid_response = MagicMock()
    valid_response.choices = [MagicMock(message=MagicMock(content=valid_content))]
    client.chat.completions.create.side_effect = [malformed_response, valid_response]

    with patch(
        "regradar.agents.analysis_agent._get_llm_client",
        return_value=(client, "llama3.1"),
    ):
        result = analyze_node(_make_state_with_chunks())

    assert result.extraction is not None
    assert client.chat.completions.create.call_count == 2


def test_analyze_node_leaves_extraction_none_after_two_malformed_responses() -> None:
    client = MagicMock()
    malformed_response = MagicMock()
    malformed_response.choices = [MagicMock(message=MagicMock(content="not valid json"))]
    client.chat.completions.create.return_value = malformed_response

    with patch(
        "regradar.agents.analysis_agent._get_llm_client",
        return_value=(client, "llama3.1"),
    ):
        result = analyze_node(_make_state_with_chunks())

    assert result.extraction is None
    assert client.chat.completions.create.call_count == 2


def test_analyze_node_rejects_out_of_range_source_chunk_index_and_retries() -> None:
    bad_json = dict(VALID_EXTRACTION_JSON)
    bad_json["obligations"] = [
        {"description": "Some obligation.", "source_chunk_index": 99}
    ]
    valid_content = json.dumps(VALID_EXTRACTION_JSON)
    client = MagicMock()
    bad_response = MagicMock()
    bad_response.choices = [MagicMock(message=MagicMock(content=json.dumps(bad_json)))]
    good_response = MagicMock()
    good_response.choices = [MagicMock(message=MagicMock(content=valid_content))]
    client.chat.completions.create.side_effect = [bad_response, good_response]

    with patch(
        "regradar.agents.analysis_agent._get_llm_client",
        return_value=(client, "llama3.1"),
    ):
        # Only 1 chunk exists (index 0) in _make_state_with_chunks(1); index 99 is invalid.
        result = analyze_node(_make_state_with_chunks(chunk_count=1))

    assert result.extraction is not None
    assert client.chat.completions.create.call_count == 2


def test_analyze_node_with_no_chunks_leaves_extraction_none() -> None:
    state = PipelineState(filing_id=uuid.uuid4(), raw_text="", chunks=None)

    with patch("regradar.agents.analysis_agent._get_llm_client") as mock_get_client:
        result = analyze_node(state)

    assert result.extraction is None
    mock_get_client.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/agents/test_analysis_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.agents.analysis_agent'`

- [ ] **Step 3: Write the implementation**

Create `src/regradar/agents/analysis_agent.py`:

```python
"""The real Analysis Agent — structured extraction via local Ollama (or
real OpenAI, untested/unexercised for now, same USE_LOCAL_LLM toggle as
triage_agent.py and dual-model voting).

analyze_node is a plain sync function — no DB access. Everything it
needs (raw_text, chunks, retrieved_chunks) is already in PipelineState;
the extractions-table INSERT happens in workers/pipeline_tasks.py after
the graph, mirroring how triage's domain/risk_level are persisted.

Citations reference real chunk_index values (not raw-text quotes) —
chunk_filing() runs before the graph in pipeline_tasks.py specifically
so this node has stable chunk identity to cite.
"""

import json
import logging

from openai import OpenAI

from regradar.agents.state import ExtractionResult, PipelineState
from regradar.core.config import get_settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2

EXTRACTION_SYSTEM_PROMPT = (
    "You are a regulatory filing analyst. Extract obligations, deadlines, risk flags, "
    "affected products, key entities, and competitor mentions from the filing text below, "
    "which is provided as a series of numbered chunks. Every obligation MUST include a "
    "source_chunk_index that is the integer index of the chunk (from the numbered list below) "
    "that supports it. Respond with strict JSON only, matching the required schema exactly."
)

EXTRACTION_RETRY_SUFFIX = (
    " The previous response was invalid — every field is required, and every obligation's "
    "source_chunk_index MUST be a valid integer index into the numbered chunk list provided. "
    "Respond with strict, schema-conformant JSON only."
)

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "obligations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "source_chunk_index": {"type": "integer"},
                },
                "required": ["description", "source_chunk_index"],
            },
        },
        "deadlines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "date": {"type": "string"},
                },
                "required": ["description", "date"],
            },
        },
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "affected_products": {"type": "array", "items": {"type": "string"}},
        "key_entities": {"type": "array", "items": {"type": "string"}},
        "competitor_mentions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "obligations",
        "deadlines",
        "risk_flags",
        "affected_products",
        "key_entities",
        "competitor_mentions",
    ],
}


class AnalysisError(Exception):
    """Raised internally when extraction fails validation after retry —
    caught by analyze_node, never propagates out of it."""


def _get_llm_client() -> tuple[OpenAI, str]:
    settings = get_settings()
    if settings.use_local_llm:
        return (
            OpenAI(base_url=settings.local_llm_base_url, api_key="ollama-local"),
            settings.local_llm_model,
        )
    return OpenAI(api_key=settings.openai_api_key.get_secret_value()), settings.tier_high_model


def _build_extraction_prompt(state: PipelineState) -> str:
    chunk_lines = "\n".join(
        f"[chunk {chunk.chunk_index}]: {chunk.chunk_text}" for chunk in (state.chunks or [])
    )
    context_lines = ""
    if state.retrieved_chunks:
        context_lines = "\n\nSimilar past filings for grounding context:\n" + "\n".join(
            f"- {rc.chunk_text}" for rc in state.retrieved_chunks
        )
    return f"Filing chunks:\n{chunk_lines}{context_lines}"


def _call_extraction_model(prompt: str, strict_retry: bool) -> dict:
    client, model = _get_llm_client()
    system_prompt = EXTRACTION_SYSTEM_PROMPT + (EXTRACTION_RETRY_SUFFIX if strict_retry else "")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "extraction", "schema": EXTRACTION_SCHEMA, "strict": True},
        },
        temperature=0,
    )
    content = response.choices[0].message.content or ""
    return json.loads(content)


def _validate_extraction(parsed: dict, chunk_count: int) -> None:
    for key in EXTRACTION_SCHEMA["required"]:
        if key not in parsed:
            raise AnalysisError(f"Missing required field: {key}")
    for obligation in parsed["obligations"]:
        idx = obligation.get("source_chunk_index")
        if not isinstance(idx, int) or not (0 <= idx < chunk_count):
            raise AnalysisError(f"Invalid source_chunk_index: {idx!r}")


def analyze_node(state: PipelineState) -> PipelineState:
    """The real analyze node — replaces AGENT-01's passthrough stub.

    On success, sets state.extraction. On failure after one retry with a
    stricter prompt, leaves state.extraction at its default None —
    workers/pipeline_tasks.py reads this as the signal to mark the
    filing needs_review instead of saving incomplete data.
    """
    if not state.chunks:
        logger.warning(
            "No chunks available for filing %s; skipping extraction", state.filing_id
        )
        return state

    prompt = _build_extraction_prompt(state)
    _, model_name = _get_llm_client()

    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            parsed = _call_extraction_model(prompt, strict_retry=attempt > 0)
            _validate_extraction(parsed, len(state.chunks))
            extraction = ExtractionResult(
                obligations=parsed["obligations"],
                deadlines=parsed["deadlines"],
                risk_flags=parsed["risk_flags"],
                affected_products=parsed["affected_products"],
                key_entities=parsed["key_entities"],
                competitor_mentions=parsed["competitor_mentions"],
                model_used=model_name,
            )
            return state.model_copy(update={"extraction": extraction})
        except (json.JSONDecodeError, AnalysisError) as exc:
            last_error = exc
            logger.warning(
                "Extraction attempt %d failed for filing %s: %s",
                attempt + 1,
                state.filing_id,
                exc,
            )

    logger.error(
        "Extraction failed for filing %s after %d attempts: %s",
        state.filing_id,
        MAX_ATTEMPTS,
        last_error,
    )
    return state
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/agents/test_analysis_agent.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/agents/analysis_agent.py tests/unit/agents/test_analysis_agent.py
git commit -m "Add structured extraction Analysis Agent (AGENT-07)"
```

---

### Task 4: Wire `analyze_node` into `graph.py`; update `test_graph.py` and `test_pipeline_graph.py`

**Files:**
- Modify: `src/regradar/agents/graph.py`
- Modify: `tests/unit/agents/test_graph.py`
- Modify: `tests/integration/test_pipeline_graph.py`

**Interfaces:**
- Consumes: `analyze_node` (Task 3).
- Produces: `build_graph()`'s compiled graph now runs the real `analyze_node`.

- [ ] **Step 1: Update `graph.py`**

Replace the stub `analyze_node` import/definition with the real one, matching the pattern already
used for `triage_node`/`retrieve_node`:

```python
"""The LangGraph supervisor graph wiring the six pipeline agents together.

triage_node, retrieve_node, and analyze_node are real implementations
(AGENT-02, AGENT-06, AGENT-07) — summarize_node/deliver_node are still
stubs for later tickets. retrieve_node is the only async node; the
graph is run via ainvoke() (not invoke()) so it can await retrieve_node's
DB query — LangGraph mixes sync and async nodes transparently in async
execution.
"""

from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from regradar.agents.analysis_agent import analyze_node
from regradar.agents.rag_retrieval_agent import retrieve_node
from regradar.agents.state import PipelineState
from regradar.agents.triage_agent import triage_node
from regradar.models.enums import RiskLevel


def summarize_node(state: PipelineState) -> PipelineState:
    """Stub — replaced by the persona-brief generator in AGENT-08."""
    return state


def deliver_node(state: PipelineState) -> PipelineState:
    """Stub — replaced by the Slack/email/webhook fan-out agent in AGENT-10."""
    return state
```

(the rest of the file — `route_after_triage`, `build_graph` — is unchanged; remove only the old
`def analyze_node(state: PipelineState) -> PipelineState: ...` stub definition)

- [ ] **Step 2: Update `tests/unit/agents/test_graph.py`**

`analyze_node` is no longer a stub, so it drops out of the stub-passthrough parametrize list.
Update the import and the parametrize:

```python
from regradar.agents.graph import (
    deliver_node,
    route_after_triage,
    summarize_node,
)
```

```python
@pytest.mark.parametrize(
    "node",
    [deliver_node, summarize_node],
)
def test_stub_node_returns_state_unchanged(node) -> None:
    state = _make_state(risk_level=RiskLevel.HIGH)

    result = node(state)

    assert result == state
```

(everything else in the file is unchanged)

- [ ] **Step 3: Update `tests/integration/test_pipeline_graph.py`**

`analyze_node` now does real work (attempts a live model call if not faked) — all three tests in
this file must monkeypatch it, the same way `triage_node` and `retrieve_node` already are. Replace
the whole file:

```python
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
```

- [ ] **Step 4: Run all affected tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/agents/test_graph.py tests/integration/test_pipeline_graph.py tests/unit/agents/test_analysis_agent.py -v`
Expected: PASS (every test in these three files)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/agents/graph.py tests/unit/agents/test_graph.py tests/integration/test_pipeline_graph.py
git commit -m "Wire real analyze_node into the graph (AGENT-07)"
```

---

### Task 5: Reorder chunking and persist extraction in `pipeline_tasks.py`

**Files:**
- Modify: `src/regradar/workers/pipeline_tasks.py`
- Modify: `tests/unit/workers/test_pipeline_tasks.py`

**Interfaces:**
- Consumes: `regradar.models.extraction.Extraction`, `FilingStatus.NEEDS_REVIEW` (Task 1).
- Produces: updated `_run_pipeline_for_filing` — same overall shape, `chunk_filing()` called
  before the graph instead of after; extraction persisted after the graph.

- [ ] **Step 1: Update the five existing tests whose mocked `ainvoke` return dicts are missing the
`"extraction"` key**

`_run_pipeline_for_filing`'s new code reads `result["extraction"]` — every existing test that
mocks `build_graph().ainvoke(...)` with a plain dict must include this key or the real code will
raise `KeyError`. In `tests/unit/workers/test_pipeline_tasks.py`, find every occurrence of a
mocked `ainvoke` return value (there are five: `test_process_filing_persists_classification_on_success`,
`test_process_filing_marks_needs_classification_when_triage_fails`,
`test_process_filing_extracts_text_and_embeds_chunks_when_pdf_present`,
`test_process_filing_falls_back_to_empty_text_when_pdf_extraction_fails`,
`test_process_filing_skips_extraction_when_no_pdf_key`) and add `"extraction": None,` to each
dict. For example, in `test_process_filing_persists_classification_on_success`:

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
                    "extraction": None,
                }
            )
        ),
    )
```

Apply the identical addition (`"extraction": None,` inside the returned dict) to all five sites,
including the `_fake_ainvoke`/`_fake_invoke`-style custom function in
`test_process_filing_falls_back_to_empty_text_when_pdf_extraction_fails` (add `"extraction":
None,` to its returned dict too).

- [ ] **Step 2: Write the new failing tests**

Add to `tests/unit/workers/test_pipeline_tasks.py`, after the existing
`test_process_filing_skips_extraction_when_no_pdf_key` test. First add the imports (alongside the
existing `from regradar.rag.chunking import Chunk` line):

```python
from regradar.agents.state import ExtractionResult
from regradar.models.extraction import Extraction
```

Then the new tests:

```python
def test_process_filing_persists_extraction_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    # ainvoke() returns nested Pydantic sub-models as real instances, not
    # dicts — verified against a real LangGraph call — so the mock here
    # must match: a real ExtractionResult, not a plain dict.
    extraction_result = ExtractionResult(
        obligations=[{"description": "File report.", "source_chunk_index": 0}],
        deadlines=[{"description": "Annual report", "date": "2027-01-01"}],
        risk_flags=["material weakness"],
        affected_products=[],
        key_entities=["Acme Corp"],
        competitor_mentions=[],
        model_used="llama3.1",
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.FINANCIAL,
                    "risk_level": RiskLevel.LOW,
                    "classification_confidence": 0.9,
                    "extraction": extraction_result,
                }
            )
        ),
    )

    process_filing.run(str(filing_id))

    mock_db.add.assert_called_once()
    added_extraction = mock_db.add.call_args[0][0]
    assert isinstance(added_extraction, Extraction)
    assert added_extraction.filing_id == filing_id
    assert added_extraction.obligations == extraction_result.obligations
    assert added_extraction.model_used == "llama3.1"
    assert filing.status == FilingStatus.CLASSIFYING


def test_process_filing_marks_needs_review_when_extraction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.FINANCIAL,
                    "risk_level": RiskLevel.LOW,
                    "classification_confidence": 0.9,
                    "extraction": None,
                }
            )
        ),
    )

    process_filing.run(str(filing_id))

    mock_db.add.assert_not_called()
    assert filing.status == FilingStatus.NEEDS_REVIEW


def test_process_filing_calls_chunk_filing_before_graph_invoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = "filings/abc123.pdf"

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    monkeypatch.setattr(
        pipeline_tasks_module, "fetch_pdf_bytes", lambda s3_key: b"fake pdf bytes"
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "extract_text_and_tables",
        lambda pdf_bytes: ("Item 1. Real extracted filing text.", []),
    )

    fake_chunks = [
        Chunk(
            chunk_index=0,
            chunk_text="Item 1. Real extracted filing text.",
            section_reference="Item 1.",
            token_count=6,
            is_table=False,
        )
    ]
    call_order: list[str] = []

    def _fake_chunk_filing(text, tables):
        call_order.append("chunk_filing")
        return fake_chunks

    captured_state = {}

    async def _fake_ainvoke(state, config=None):
        call_order.append("ainvoke")
        captured_state["chunks"] = state.chunks
        return {
            "domain": FilingDomain.FINANCIAL,
            "risk_level": RiskLevel.LOW,
            "classification_confidence": 0.9,
            "extraction": None,
        }

    monkeypatch.setattr(pipeline_tasks_module, "chunk_filing", _fake_chunk_filing)
    monkeypatch.setattr(
        pipeline_tasks_module, "build_graph", lambda: MagicMock(ainvoke=_fake_ainvoke)
    )
    mock_embed_chunks = AsyncMock()
    monkeypatch.setattr(pipeline_tasks_module, "embed_chunks", mock_embed_chunks)

    process_filing.run(str(filing_id))

    assert call_order == ["chunk_filing", "ainvoke"]
    assert captured_state["chunks"] == fake_chunks
    mock_embed_chunks.assert_awaited_once_with(filing_id, fake_chunks, mock_db)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/workers/test_pipeline_tasks.py -v -k "persists_extraction or needs_review or chunk_filing_before"`
Expected: FAIL — `test_process_filing_persists_extraction_on_success` and
`test_process_filing_marks_needs_review_when_extraction_fails` fail with `KeyError: 'extraction'`
(current code doesn't read this key); `test_process_filing_calls_chunk_filing_before_graph_invoke`
fails because `state.chunks` isn't populated (current code doesn't pass `chunks` to
`PipelineState`, and `chunk_filing` is currently called after `ainvoke`, not before).

- [ ] **Step 4: Write the implementation**

In `src/regradar/workers/pipeline_tasks.py`, add the import (alongside the existing
`from regradar.models.filing import Filing` line):

```python
from regradar.models.extraction import Extraction
```

Replace `_run_pipeline_for_filing`:

```python
async def _run_pipeline_for_filing(filing_id: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as db:
        filing = await db.get(Filing, uuid.UUID(filing_id))
        if filing is None:
            logger.warning("Filing %s not found — skipping pipeline run", filing_id)
            return

        raw_text = ""
        chunks: list = []
        if filing.raw_pdf_s3_key:
            try:
                pdf_bytes = fetch_pdf_bytes(filing.raw_pdf_s3_key)
                raw_text, tables = extract_text_and_tables(pdf_bytes)
                if raw_text:
                    chunks = chunk_filing(raw_text, tables)
            except Exception as exc:  # noqa: BLE001 — never crash the pipeline over a bad/missing PDF
                logger.warning("PDF extraction failed for filing %s: %s", filing_id, exc)

        state = PipelineState(filing_id=filing.id, raw_text=raw_text, chunks=chunks or None)
        result = await build_graph().ainvoke(state, config={"configurable": {"db": db}})

        if result["domain"] is None:
            filing.status = FilingStatus.NEEDS_CLASSIFICATION
        elif result["extraction"] is None and chunks:
            filing.status = FilingStatus.NEEDS_REVIEW
        else:
            filing.domain = result["domain"]
            filing.risk_level = result["risk_level"]
            filing.classification_confidence = result["classification_confidence"]
            filing.status = FilingStatus.CLASSIFYING
        await db.commit()

        if result["extraction"] is not None:
            # result["extraction"] is a real ExtractionResult instance —
            # ainvoke() does not flatten nested Pydantic sub-models into
            # plain dicts (verified) — so this uses attribute access and
            # model_dump(), never dict-subscript access.
            extraction_result = result["extraction"]
            db.add(
                Extraction(
                    filing_id=filing.id,
                    obligations=extraction_result.obligations,
                    deadlines=extraction_result.deadlines,
                    risk_flags=extraction_result.risk_flags,
                    affected_products=extraction_result.affected_products,
                    key_entities=extraction_result.key_entities,
                    competitor_mentions=extraction_result.competitor_mentions,
                    model_used=extraction_result.model_used,
                    raw_model_response=extraction_result.model_dump(),
                )
            )
            await db.commit()

        if raw_text:
            await embed_chunks(filing.id, chunks, db)
```

Note the `elif result["extraction"] is None and chunks:` branch: `NEEDS_REVIEW` only applies when
extraction was actually attempted (chunks existed) and failed — not when there was never any text
to extract from in the first place (that case already falls through to the normal
domain/risk_level branch, since `analyze_node` itself no-ops without chunks and doesn't touch
`state.extraction`, but the *filing*'s classification still succeeded). This matches Task 3's
`test_analyze_node_with_no_chunks_leaves_extraction_none` — a filing with no PDF text at all
still gets a normal `CLASSIFYING` status from a successful triage, not `NEEDS_REVIEW`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/workers/test_pipeline_tasks.py -v`
Expected: PASS (all tests in the file — the 5 updated pre-existing tests, the 3 new ones, and
every other test untouched by this change)

- [ ] **Step 6: Commit**

```bash
git add src/regradar/workers/pipeline_tasks.py tests/unit/workers/test_pipeline_tasks.py
git commit -m "Reorder chunking before the graph; persist extraction result (AGENT-07)"
```

---

### Task 6: Live verification, full-suite check, lint, mypy, push

**Files:** none (verification only)

- [ ] **Step 1: Live-verify extraction against real Ollama**

Start Ollama briefly:

```bash
/opt/homebrew/opt/ollama/bin/ollama serve > /tmp/ollama-serve.log 2>&1 &
```

Wait for it to be ready (`curl -s http://localhost:11434`). Confirm `llama3.1` is still pulled:
`/opt/homebrew/opt/ollama/bin/ollama list`.

Run a quick interactive script (adjust as needed) calling `analyze_node` directly against a
`PipelineState` with a few real `Chunk` objects built from realistic filing text (e.g., adapt the
`Item 1A. Risk Factors` example already used during design verification — a material weakness
disclosure with two dated obligations). Confirm the result has `extraction` populated with
sensible `obligations`/`deadlines`/`risk_flags`, and that every obligation's implied
`source_chunk_index` was valid (already enforced by `_validate_extraction`, so simply confirming
`result.extraction is not None` on the first attempt confirms this end-to-end).

Expected: no errors; `analyze_node` returns a populated `ExtractionResult` from a single real
Ollama call.

- [ ] **Step 2: Stop Ollama**

```bash
pkill -f "ollama serve"
```

Confirm it's down: `curl -s http://localhost:11434` should fail to connect.

- [ ] **Step 3: Run the full default test suite**

Run: `.venv/bin/pytest -v --ignore=tests/integration/test_flows.py`
Expected: PASS (all tests; `test_flows.py` needs a live Postgres this ticket's default test run
doesn't start, consistent with prior tickets).

- [ ] **Step 4: Run lint and type checks**

Run: `.venv/bin/ruff check src/regradar/agents src/regradar/models/enums.py src/regradar/workers/pipeline_tasks.py tests/unit/agents tests/integration/test_pipeline_graph.py tests/unit/workers/test_pipeline_tasks.py`
Run: `.venv/bin/mypy src/regradar/agents src/regradar/models/enums.py src/regradar/workers/pipeline_tasks.py`
Expected: no errors. Fix any and commit the fix as part of this task.

- [ ] **Step 5: Push the branch**

```bash
git push -u origin agent-07-analysis-agent
```

Do not merge to `master` — merging is a separate explicit step the user confirms.
