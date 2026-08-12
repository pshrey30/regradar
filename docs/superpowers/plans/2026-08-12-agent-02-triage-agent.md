# AGENT-02 — Triage Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace AGENT-01's passthrough `triage_node` stub with a real zero-shot classifier that
calls Hugging Face's hosted `facebook/bart-large-mnli` model to assign a filing's `domain`, derives
an initial `risk_level` via a keyword+confidence heuristic, and persists the result (or a
`needs_classification` fallback on failure) to the `Filing` row.

**Architecture:** A new `src/regradar/agents/triage_agent.py` module owns the HF HTTP call, the
risk heuristic, and the real `triage_node` — all pure functions, no DB access, matching AGENT-01's
node design. `agents/graph.py` swaps its stub `triage_node` for an import of the real one.
`workers/pipeline_tasks.py`'s `_run_pipeline_for_filing` gains the DB persistence step, and its
`async with` session block is restructured so persistence happens before the session closes (a
latent bug from AGENT-01, harmless until now since nothing was written back).

**Tech Stack:** `httpx` (already a dependency, used synchronously elsewhere in this codebase —
see `ingestion/sources/sec_edgar.py`), Hugging Face's Inference Providers HTTP API (no SDK),
Alembic `autocommit_block()` for the enum-value migration.

## Global Constraints

- Design source of truth: `docs/superpowers/specs/2026-08-12-agent-02-triage-agent-design.md`.
- HF endpoint: `https://router.huggingface.co/hf-inference/models/facebook/bart-large-mnli` — the
  ticket's literal spec cites a dead URL (`api-inference.huggingface.co`); do not use it.
- Candidate labels: `["financial", "clinical", "environmental", "other"]`, in that exact order.
- Retry policy: on HTTP error or timeout, sleep 2 seconds, retry once. If the retry also fails,
  raise `TriageClassificationError` — never guess a classification.
- Risk heuristic order (critical keywords → high keywords → confidence check → default LOW) is
  fixed — keyword checks run before the confidence check, so a low-confidence-but-severe filing is
  never downgraded to MEDIUM.
- No `torch`/`transformers`/local-inference work in this ticket — hosted API only.
- No DB access inside `triage_node` or any other graph node — all persistence happens in
  `workers/pipeline_tasks.py`.
- Automated tests must never call the real HF API by default. Exactly one test file
  (`tests/unit/agents/test_triage_live_smoke.py`) is allowed to, and only when explicitly run via
  `pytest -m live` — never as part of a default `pytest` invocation or CI.
- `compiled_graph.invoke(state)` returns a plain `dict`, not a `PipelineState` (verified in
  AGENT-01) — persistence code reads `result["domain"]`, not `result.domain`.
- Settings access pattern: `get_settings().huggingface_api_token.get_secret_value()` (see
  `core/config.py` and how `ingestion/sources/sec_edgar.py` reads `sec_edgar_user_agent`).

---

### Task 1: `classify_filing` — HF HTTP call with retry

**Files:**
- Create: `src/regradar/agents/triage_agent.py`
- Test: `tests/unit/agents/test_triage_agent.py`

**Interfaces:**
- Consumes: `regradar.core.config.get_settings()` (`.huggingface_api_token.get_secret_value()`),
  `regradar.models.enums.FilingDomain`.
- Produces (for Task 2 and Task 3):
  - `class TriageClassificationError(Exception)`
  - `class ClassificationResult(BaseModel)`: fields `domain: FilingDomain`, `confidence: float`,
    `raw_scores: dict[str, float]`.
  - `def classify_filing(text: str) -> ClassificationResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/agents/test_triage_agent.py`:

```python
"""Unit tests for the Triage Agent's HF classification call, risk heuristic,
and the real triage_node. All HTTP calls are mocked — see
test_triage_live_smoke.py for the one test allowed to hit the real API.
"""

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from regradar.agents.triage_agent import (
    ClassificationResult,
    TriageClassificationError,
    classify_filing,
)
from regradar.models.enums import FilingDomain


def _mock_response(json_body: list[dict], status_code: int = 200) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = json_body
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=response
        )
    return response


HF_SUCCESS_BODY = [
    {"label": "financial", "score": 0.87},
    {"label": "other", "score": 0.08},
    {"label": "clinical", "score": 0.03},
    {"label": "environmental", "score": 0.02},
]


def test_classify_filing_returns_top_label_and_confidence() -> None:
    with patch("regradar.agents.triage_agent.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(HF_SUCCESS_BODY)

        result = classify_filing("Some filing text about financial disclosures.")

        assert result.domain == FilingDomain.FINANCIAL
        assert result.confidence == pytest.approx(0.87)
        assert result.raw_scores["financial"] == pytest.approx(0.87)


def test_classify_filing_sends_expected_candidate_labels_and_url() -> None:
    with patch("regradar.agents.triage_agent.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(HF_SUCCESS_BODY)

        classify_filing("some text")

        args, kwargs = mock_post.call_args
        assert args[0] == (
            "https://router.huggingface.co/hf-inference/models/facebook/bart-large-mnli"
        )
        assert kwargs["json"]["parameters"]["candidate_labels"] == [
            "financial",
            "clinical",
            "environmental",
            "other",
        ]


def test_classify_filing_retries_once_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with patch("regradar.agents.triage_agent.httpx.post") as mock_post:
        mock_post.side_effect = [
            httpx.RequestError("connection failed", request=MagicMock()),
            _mock_response(HF_SUCCESS_BODY),
        ]

        result = classify_filing("some text")

        assert result.domain == FilingDomain.FINANCIAL
        assert mock_post.call_count == 2


def test_classify_filing_raises_after_both_attempts_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with patch("regradar.agents.triage_agent.httpx.post") as mock_post:
        mock_post.side_effect = httpx.RequestError("connection failed", request=MagicMock())

        with pytest.raises(TriageClassificationError):
            classify_filing("some text")

        assert mock_post.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/agents/test_triage_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.agents.triage_agent'`

- [ ] **Step 3: Write the implementation**

Create `src/regradar/agents/triage_agent.py`:

```python
"""The real Triage Agent — zero-shot classification via Hugging Face.

Replaces AGENT-01's passthrough triage_node stub with a call to HF's
hosted facebook/bart-large-mnli model, plus a deterministic keyword +
confidence heuristic for an initial risk_level. This module is pure —
no DB access — matching every other node in agents/graph.py; DB
persistence happens in workers/pipeline_tasks.py.
"""

import logging
import time

import httpx
from pydantic import BaseModel

from regradar.agents.state import PipelineState
from regradar.core.config import get_settings
from regradar.models.enums import FilingDomain, RiskLevel

logger = logging.getLogger(__name__)

HF_TRIAGE_MODEL_URL = "https://router.huggingface.co/hf-inference/models/facebook/bart-large-mnli"
CANDIDATE_LABELS = ["financial", "clinical", "environmental", "other"]

CRITICAL_KEYWORDS = [
    "material weakness",
    "restatement",
    "fraud",
    "going concern",
    "cease and desist",
    "consent decree",
    "delisting",
    "class action",
]
HIGH_KEYWORDS = [
    "deficiency",
    "non-compliance",
    "violation",
    "penalty",
    "recall",
    "warning letter",
    "sec investigation",
]

LOW_CONFIDENCE_THRESHOLD = 0.5


class TriageClassificationError(Exception):
    """Raised when HF classification fails after one retry."""


class ClassificationResult(BaseModel):
    domain: FilingDomain
    confidence: float
    raw_scores: dict[str, float]


def classify_filing(text: str) -> ClassificationResult:
    """Call HF's zero-shot classification endpoint for facebook/bart-large-mnli.

    Retries once after a 2-second delay on any request error or non-2xx
    response. Raises TriageClassificationError if the retry also fails —
    the caller (triage_node) must not guess a classification.
    """
    token = get_settings().huggingface_api_token.get_secret_value()
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"inputs": text, "parameters": {"candidate_labels": CANDIDATE_LABELS}}

    last_error: Exception | None = None
    for attempt in range(2):
        if attempt > 0:
            time.sleep(2)
        start = time.monotonic()
        try:
            response = httpx.post(HF_TRIAGE_MODEL_URL, headers=headers, json=payload, timeout=30.0)
            response.raise_for_status()
            body = response.json()
            latency_ms = (time.monotonic() - start) * 1000
            raw_scores = {item["label"]: item["score"] for item in body}
            top = max(body, key=lambda item: item["score"])
            logger.info(
                "HF triage classification: domain=%s confidence=%.3f latency_ms=%.0f",
                top["label"],
                top["score"],
                latency_ms,
            )
            return ClassificationResult(
                domain=FilingDomain(top["label"]),
                confidence=top["score"],
                raw_scores=raw_scores,
            )
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            last_error = exc
            logger.warning("HF triage classification attempt %d failed: %s", attempt + 1, exc)

    raise TriageClassificationError(
        f"HF classification failed after retry: {last_error}"
    ) from last_error
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/agents/test_triage_agent.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/agents/triage_agent.py tests/unit/agents/test_triage_agent.py
git commit -m "Add HF zero-shot classification call for the Triage Agent (AGENT-02)"
```

---

### Task 2: `derive_risk_level` heuristic

**Files:**
- Modify: `src/regradar/agents/triage_agent.py`
- Test: `tests/unit/agents/test_triage_agent.py`

**Interfaces:**
- Consumes: `regradar.models.enums.RiskLevel`, `FilingDomain` (Task 1).
- Produces (for Task 3): `def derive_risk_level(domain: FilingDomain, confidence: float, text: str) -> RiskLevel`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/agents/test_triage_agent.py`:

```python
from regradar.agents.triage_agent import derive_risk_level
from regradar.models.enums import RiskLevel


def test_derive_risk_level_critical_keyword_overrides_high_confidence() -> None:
    text = "The company disclosed a material weakness in internal controls."

    result = derive_risk_level(FilingDomain.FINANCIAL, confidence=0.95, text=text)

    assert result == RiskLevel.CRITICAL


def test_derive_risk_level_high_keyword() -> None:
    text = "The FDA issued a warning letter regarding manufacturing practices."

    result = derive_risk_level(FilingDomain.CLINICAL, confidence=0.9, text=text)

    assert result == RiskLevel.HIGH


def test_derive_risk_level_low_confidence_with_no_keywords_is_medium() -> None:
    text = "Routine quarterly filing with no notable events."

    result = derive_risk_level(FilingDomain.FINANCIAL, confidence=0.3, text=text)

    assert result == RiskLevel.MEDIUM


def test_derive_risk_level_confident_and_clean_is_low() -> None:
    text = "Routine quarterly filing with no notable events."

    result = derive_risk_level(FilingDomain.FINANCIAL, confidence=0.9, text=text)

    assert result == RiskLevel.LOW


def test_derive_risk_level_low_confidence_but_critical_keyword_is_still_critical() -> None:
    text = "Preliminary indication of possible fraud under review."

    result = derive_risk_level(FilingDomain.FINANCIAL, confidence=0.2, text=text)

    assert result == RiskLevel.CRITICAL
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/agents/test_triage_agent.py -v -k derive_risk_level`
Expected: FAIL with `ImportError: cannot import name 'derive_risk_level'`

- [ ] **Step 3: Write the implementation**

Add to `src/regradar/agents/triage_agent.py` (after the `LOW_CONFIDENCE_THRESHOLD` constant, before
`classify_filing`, or anywhere below the constants — order doesn't matter as long as it's a
module-level function):

```python
def derive_risk_level(domain: FilingDomain, confidence: float, text: str) -> RiskLevel:
    """Deterministic keyword + confidence heuristic for an initial risk_level.

    Rule set, in order:
      1. CRITICAL if `text` (case-insensitive) contains any critical-severity
         keyword.
      2. HIGH if `text` contains any high-severity keyword.
      3. MEDIUM if `confidence` < LOW_CONFIDENCE_THRESHOLD — an uncertain
         classification is flagged for review, never silently treated as LOW.
      4. LOW otherwise (confident classification, no risk language detected).

    Keyword checks run before the confidence check, so a low-confidence but
    clearly severe filing is still flagged CRITICAL/HIGH, not downgraded to
    MEDIUM by the confidence rule. `domain` is accepted for future
    domain-specific keyword rules but unused by the current rule set.
    """
    lowered = text.lower()

    if any(keyword in lowered for keyword in CRITICAL_KEYWORDS):
        return RiskLevel.CRITICAL
    if any(keyword in lowered for keyword in HIGH_KEYWORDS):
        return RiskLevel.HIGH
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/agents/test_triage_agent.py -v`
Expected: PASS (9 tests total: 4 from Task 1 + 5 from this task)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/agents/triage_agent.py tests/unit/agents/test_triage_agent.py
git commit -m "Add risk_level heuristic for the Triage Agent (AGENT-02)"
```

---

### Task 3: Real `triage_node`, wired into `graph.py`

**Files:**
- Modify: `src/regradar/agents/triage_agent.py`
- Modify: `src/regradar/agents/graph.py`
- Test: `tests/unit/agents/test_triage_agent.py`
- Test: `tests/unit/agents/test_graph.py` (existing stub-passthrough test for `triage_node` no
  longer applies — see Step 1)

**Interfaces:**
- Consumes: `classify_filing`, `derive_risk_level`, `TriageClassificationError` (Tasks 1-2),
  `regradar.agents.state.PipelineState`.
- Produces: `def triage_node(state: PipelineState) -> PipelineState` — the real implementation,
  imported by `agents/graph.py`.

- [ ] **Step 1: Update the existing stub-passthrough test in `test_graph.py`**

`tests/unit/agents/test_graph.py` currently parametrizes `test_stub_node_returns_state_unchanged`
over all five nodes including `triage_node` — that assumption is no longer true once `triage_node`
does real work. Edit `tests/unit/agents/test_graph.py`:

```python
from regradar.agents.graph import (
    analyze_node,
    deliver_node,
    retrieve_node,
    route_after_triage,
    summarize_node,
)
```

(remove `triage_node` from this import — it now comes from `triage_agent`, not `graph`, and its
behavior is tested in `test_triage_agent.py`, not here)

```python
@pytest.mark.parametrize(
    "node",
    [retrieve_node, analyze_node, summarize_node, deliver_node],
)
def test_stub_node_returns_state_unchanged(node) -> None:
    state = _make_state(risk_level=RiskLevel.HIGH)

    result = node(state)

    assert result == state
```

(the same change — drop `triage_node` from the parametrize list; everything else in this test
function body is unchanged)

Now write the new failing test in `tests/unit/agents/test_triage_agent.py`:

```python
import uuid
from unittest.mock import patch

from regradar.agents.state import PipelineState
from regradar.agents.triage_agent import ClassificationResult, TriageClassificationError, triage_node


def _make_state() -> PipelineState:
    return PipelineState(filing_id=uuid.uuid4(), raw_text="Routine filing text.")


def test_triage_node_populates_state_on_success() -> None:
    fake_result = ClassificationResult(
        domain=FilingDomain.FINANCIAL, confidence=0.9, raw_scores={"financial": 0.9}
    )
    with patch("regradar.agents.triage_agent.classify_filing", return_value=fake_result):
        state = triage_node(_make_state())

    assert state.domain == FilingDomain.FINANCIAL
    assert state.classification_confidence == 0.9
    assert state.risk_level == RiskLevel.LOW


def test_triage_node_leaves_state_unclassified_on_failure() -> None:
    with patch(
        "regradar.agents.triage_agent.classify_filing",
        side_effect=TriageClassificationError("boom"),
    ):
        state = triage_node(_make_state())

    assert state.domain is None
    assert state.classification_confidence is None
    assert state.risk_level is None
```

Add the matching imports at the top of `tests/unit/agents/test_triage_agent.py` if not already
present from earlier tasks: `from regradar.models.enums import FilingDomain, RiskLevel`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/agents/test_triage_agent.py tests/unit/agents/test_graph.py -v`
Expected: FAIL — `ImportError: cannot import name 'triage_node' from 'regradar.agents.triage_agent'`

- [ ] **Step 3: Write the implementation**

Add to `src/regradar/agents/triage_agent.py` (at the end of the file):

```python
def triage_node(state: PipelineState) -> PipelineState:
    """The real triage node — replaces AGENT-01's passthrough stub.

    On success, sets state.domain, state.classification_confidence, and
    state.risk_level. On TriageClassificationError, leaves those three
    fields at their default None — workers/pipeline_tasks.py reads this
    as the signal to mark the filing needs_classification instead of
    guessing.
    """
    try:
        result = classify_filing(state.raw_text)
    except TriageClassificationError:
        logger.error("Triage classification failed for filing %s; leaving unclassified", state.filing_id)
        return state

    risk_level = derive_risk_level(result.domain, result.confidence, state.raw_text)
    return state.model_copy(
        update={
            "domain": result.domain,
            "classification_confidence": result.confidence,
            "risk_level": risk_level,
        }
    )
```

Edit `src/regradar/agents/graph.py`:

```python
"""The LangGraph supervisor graph wiring the six pipeline agents together.

triage_node is the real implementation (AGENT-02) — every other node
here is still a stub for a later ticket (AGENT-06, AGENT-07, AGENT-08,
AGENT-10). The graph wiring and the triage routing decision are the
real, permanent parts of this module.
"""

from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from regradar.agents.state import PipelineState
from regradar.agents.triage_agent import triage_node
from regradar.models.enums import RiskLevel


def retrieve_node(state: PipelineState) -> PipelineState:
    """Stub — replaced by the hybrid BM25 + vector retriever in AGENT-06."""
    return state
```

(delete the old `def triage_node(state: PipelineState) -> PipelineState: ... return state` stub
definition entirely — it's replaced by the import above. Every other function in `graph.py`
—`analyze_node`, `summarize_node`, `deliver_node`, `route_after_triage`, `build_graph`—is
unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/agents -v`
Expected: PASS (all tests in `test_state.py`, `test_graph.py`, `test_triage_agent.py`)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/agents/triage_agent.py src/regradar/agents/graph.py tests/unit/agents/test_graph.py tests/unit/agents/test_triage_agent.py
git commit -m "Wire the real triage_node into the pipeline graph (AGENT-02)"
```

---

### Task 4: `needs_classification` DB enum value + migration

**Files:**
- Modify: `src/regradar/models/enums.py`
- Create: `migrations/versions/0004_add_needs_classification_status.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `FilingStatus.NEEDS_CLASSIFICATION` (value `"needs_classification"`), for Task 5.

- [ ] **Step 1: Add the enum member**

Edit `src/regradar/models/enums.py`:

```python
class FilingStatus(str, enum.Enum):
    INGESTED = "ingested"
    CLASSIFYING = "classifying"
    NEEDS_CLASSIFICATION = "needs_classification"
    RETRIEVING = "retrieving"
    ANALYZING = "analyzing"
    SUMMARIZING = "summarizing"
    DELIVERING = "delivering"
    COMPLETE = "complete"
    FAILED = "failed"
```

- [ ] **Step 2: Write the migration**

Create `migrations/versions/0004_add_needs_classification_status.py`:

```python
"""Add needs_classification to the filing_status enum.

AGENT-02's Triage Agent sets this status when HF classification fails
after a retry, instead of guessing a domain/risk_level.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

NEW_VALUE = "needs_classification"
ORIGINAL_VALUES = [
    "ingested",
    "classifying",
    "retrieving",
    "analyzing",
    "summarizing",
    "delivering",
    "complete",
    "failed",
]


def upgrade() -> None:
    # Postgres requires ALTER TYPE ... ADD VALUE to run outside an explicit
    # transaction.
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE filing_status ADD VALUE '{NEW_VALUE}'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE. Standard workaround:
    # reassign any rows using the value, then recreate the type without it.
    op.execute(
        f"UPDATE filings SET status = 'failed' WHERE status = '{NEW_VALUE}'"
    )
    values_sql = ", ".join(f"'{v}'" for v in ORIGINAL_VALUES)
    op.execute(f"CREATE TYPE filing_status_old AS ENUM ({values_sql})")
    op.execute(
        "ALTER TABLE filings ALTER COLUMN status TYPE filing_status_old "
        "USING status::text::filing_status_old"
    )
    op.execute("DROP TYPE filing_status")
    op.execute("ALTER TYPE filing_status_old RENAME TO filing_status")
```

- [ ] **Step 3: Run the migration against a real Postgres to verify it works**

This requires a running Postgres (e.g. `docker compose -f infra/docker-compose.yml up -d postgres`
briefly — per the project's cost/supervision policy, stop it again after this check, don't leave
it running unattended).

Run: `.venv/bin/alembic upgrade head`
Expected: no errors; migration `0004` applies cleanly on top of `0003`.

Run: `.venv/bin/alembic downgrade -1`
Expected: no errors; `filing_status` enum reverts to its original 8 values.

Run: `.venv/bin/alembic upgrade head` again to leave the DB at head for the rest of this plan's
work, then stop the Postgres container if you started it just for this check:
`docker compose -f infra/docker-compose.yml stop postgres`.

- [ ] **Step 4: Commit**

```bash
git add src/regradar/models/enums.py migrations/versions/0004_add_needs_classification_status.py
git commit -m "Add needs_classification filing status for failed triage (AGENT-02)"
```

---

### Task 5: Persist classification result in `process_filing`

**Files:**
- Modify: `src/regradar/workers/pipeline_tasks.py`
- Modify: `tests/unit/workers/test_pipeline_tasks.py`

**Interfaces:**
- Consumes: `FilingStatus.NEEDS_CLASSIFICATION` (Task 4), `build_graph()` (returns a `dict` with
  keys `domain`, `risk_level`, `classification_confidence`, per AGENT-01's verified `.invoke()`
  behavior).
- Produces: updated `_run_pipeline_for_filing` — no signature change, but now persists the
  classification result to the `Filing` row before the DB session closes (fixing a latent bug:
  AGENT-01's version loaded the filing, exited the `async with` block, and only *then* called
  `build_graph().invoke()` — any write-back after that point would hit a closed session).

- [ ] **Step 1: Write the failing tests**

In `tests/unit/workers/test_pipeline_tasks.py`, replace
`test_process_filing_runs_the_pipeline_graph` with two more specific tests. First, add this import
near the top (alongside the existing `from regradar.models.filing import Filing`):

```python
from regradar.models.enums import FilingDomain, RiskLevel
```

Then replace the old test:

```python
def test_process_filing_persists_classification_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()

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
            invoke=lambda state: {
                "domain": FilingDomain.FINANCIAL,
                "risk_level": RiskLevel.LOW,
                "classification_confidence": 0.9,
            }
        ),
    )

    process_filing.run(str(filing_id))

    mock_db.get.assert_awaited_once_with(Filing, filing_id)
    assert filing.domain == FilingDomain.FINANCIAL
    assert filing.risk_level == RiskLevel.LOW
    assert filing.classification_confidence == 0.9
    assert filing.status == FilingStatus.CLASSIFYING
    mock_db.commit.assert_awaited_once()


def test_process_filing_marks_needs_classification_when_triage_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()

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
            invoke=lambda state: {
                "domain": None,
                "risk_level": None,
                "classification_confidence": None,
            }
        ),
    )

    process_filing.run(str(filing_id))

    assert filing.status == FilingStatus.NEEDS_CLASSIFICATION
    mock_db.commit.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/workers/test_pipeline_tasks.py -v -k persists_classification`
Expected: FAIL — `filing.domain` was never set because `build_graph().invoke()`'s result is
currently discarded entirely by `_run_pipeline_for_filing`.

- [ ] **Step 3: Write the implementation**

Replace `_run_pipeline_for_filing` in `src/regradar/workers/pipeline_tasks.py`:

```python
async def _run_pipeline_for_filing(filing_id: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as db:
        filing = await db.get(Filing, uuid.UUID(filing_id))
        if filing is None:
            logger.warning("Filing %s not found — skipping pipeline run", filing_id)
            return

        # Real PDF-to-text extraction doesn't exist yet — AGENT-04's
        # chunking work extracts filing text from the stored S3 PDF. Until
        # then, the pipeline runs against an empty raw_text.
        state = PipelineState(filing_id=filing.id, raw_text="")
        result = build_graph().invoke(state)

        if result["domain"] is None:
            filing.status = FilingStatus.NEEDS_CLASSIFICATION
        else:
            filing.domain = result["domain"]
            filing.risk_level = result["risk_level"]
            filing.classification_confidence = result["classification_confidence"]
            filing.status = FilingStatus.CLASSIFYING
        await db.commit()
```

Note this moves the `state = PipelineState(...)` / `build_graph().invoke(state)` lines from
*after* the `async with` block (AGENT-01's version) to *inside* it — the write-back and
`db.commit()` require the session to still be open.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/workers/test_pipeline_tasks.py -v`
Expected: PASS (all tests in the file — the two new ones plus the pre-existing
`test_enqueue_filing_processing_calls_delay_with_str_id`,
`test_process_filing_skips_pipeline_when_filing_not_found`, and the `_mark_filing_failed`/
`on_failure` tests, all untouched by this change)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/workers/pipeline_tasks.py tests/unit/workers/test_pipeline_tasks.py
git commit -m "Persist triage classification result in process_filing (AGENT-02)"
```

---

### Task 6: Live smoke-test fixture + marker registration

**Files:**
- Create: `tests/unit/agents/fixtures/triage_smoke_set.json`
- Create: `tests/unit/agents/test_triage_live_smoke.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `classify_filing` (Task 1).
- Produces: nothing consumed by later tasks — this is the final verification task.

- [ ] **Step 1: Register the `live` pytest marker, excluded by default**

Edit `pyproject.toml`'s `[tool.pytest.ini_options]` section:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "live: hits a real external API — excluded by default, run explicitly with `pytest -m live`",
]
addopts = "-m 'not live'"
```

- [ ] **Step 2: Write the fixture set**

Create `tests/unit/agents/fixtures/triage_smoke_set.json`:

```json
[
  {"text": "The Company filed its Annual Report on Form 10-K disclosing revenue growth of 12% and no material weaknesses in internal controls.", "expected_domain": "financial"},
  {"text": "The registrant reported a decline in quarterly earnings and updated its risk factors related to interest rate exposure.", "expected_domain": "financial"},
  {"text": "Shareholders approved an increase in the company's authorized share capital at the annual general meeting.", "expected_domain": "financial"},
  {"text": "The FDA approved a new drug application for a treatment targeting chronic migraine following successful Phase 3 clinical trials.", "expected_domain": "clinical"},
  {"text": "A voluntary recall was issued for a batch of injectable medication due to a labeling error identified during routine quality control.", "expected_domain": "clinical"},
  {"text": "The company announced results from a Phase 2 clinical trial evaluating a novel treatment for type 2 diabetes.", "expected_domain": "clinical"},
  {"text": "The facility reported an unplanned release of wastewater exceeding permitted discharge limits into a nearby river.", "expected_domain": "environmental"},
  {"text": "The company submitted its annual greenhouse gas emissions report to the Environmental Protection Agency.", "expected_domain": "environmental"},
  {"text": "An environmental impact assessment was filed ahead of the proposed expansion of the manufacturing plant.", "expected_domain": "environmental"},
  {"text": "The board announced the appointment of a new independent director to serve a three-year term.", "expected_domain": "other"},
  {"text": "The company changed its corporate headquarters address and updated its registered agent information.", "expected_domain": "other"},
  {"text": "A routine administrative notice was filed updating the company's fiscal year end date.", "expected_domain": "other"}
]
```

- [ ] **Step 3: Write the live smoke test**

Create `tests/unit/agents/test_triage_live_smoke.py`:

```python
"""Live smoke test for the Triage Agent's HF classification call.

This is the ONE test allowed to hit the real Hugging Face API. It's
marked `live` and excluded from default pytest runs (see pyproject.toml's
addopts) — run it explicitly with `pytest -m live` when you actually want
to verify the live integration. Never wire this into CI or any
automatically-scheduled run: this project's cost/supervision policy is
that paid API calls only happen when a human explicitly asks for one.

This asserts a loose accuracy bar (>=80% on a 12-example set) — it is a
smoke test that classify_filing() behaves sensibly against a small hand-
labeled set, not the ticket's rigorous 90%/100-filing benchmark. That
real benchmark is EVAL-03's job, once a proper labeled set exists.
"""

import json
from pathlib import Path

import pytest

from regradar.agents.triage_agent import classify_filing

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "triage_smoke_set.json"


@pytest.mark.live
def test_classify_filing_live_smoke_test() -> None:
    examples = json.loads(FIXTURE_PATH.read_text())

    correct = 0
    for example in examples:
        result = classify_filing(example["text"])
        if result.domain.value == example["expected_domain"]:
            correct += 1

    accuracy = correct / len(examples)
    assert accuracy >= 0.8, f"Live smoke test accuracy {accuracy:.2%} below 80% threshold"
```

- [ ] **Step 4: Run the default suite to confirm the live test is excluded**

Run: `.venv/bin/pytest tests/unit/agents -v`
Expected: `test_classify_filing_live_smoke_test` does NOT appear in the collected/run tests (it's
deselected by `addopts = "-m 'not live'"`); all other tests in `tests/unit/agents` PASS.

- [ ] **Step 5: Run the live test explicitly, once, to verify the real integration works**

Run: `.venv/bin/pytest tests/unit/agents/test_triage_live_smoke.py -v -m live`
Expected: PASS — this makes 12 real calls to the Hugging Face API (well within the free monthly
credit). This is the one explicit, human-requested live check for this ticket; do not re-run it
repeatedly or wire it into any automated/scheduled process afterward.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/unit/agents/fixtures/triage_smoke_set.json tests/unit/agents/test_triage_live_smoke.py
git commit -m "Add live smoke test and fixture set for the Triage Agent (AGENT-02)"
```

---

### Task 7: Full-suite check, lint, mypy, push

**Files:** none (verification only)

- [ ] **Step 1: Run the full default test suite**

Run: `.venv/bin/pytest -v`
Expected: PASS (the `live`-marked test is excluded by default; the two pre-existing
`tests/integration/test_flows.py` tests may still error locally if Postgres isn't running — that's
a pre-existing condition unrelated to this branch, confirmed during AGENT-01).

- [ ] **Step 2: Run lint and type checks**

Run: `.venv/bin/ruff check src/regradar/agents src/regradar/workers/pipeline_tasks.py src/regradar/models/enums.py tests/unit/agents tests/unit/workers/test_pipeline_tasks.py`
Run: `.venv/bin/mypy src/regradar/agents src/regradar/workers/pipeline_tasks.py`
Expected: no errors. Fix any and commit the fix as part of this task.

- [ ] **Step 3: Push the branch**

```bash
git push -u origin agent-02-triage-agent
```

Do not merge to `master` — per project convention, merging is a separate explicit step the user
confirms.
