# AGENT-02 — Triage Agent (Zero-Shot Classification)

## Context

AGENT-01 (merged to `master`) built `PipelineState` (`src/regradar/agents/state.py`) and the
LangGraph supervisor graph (`src/regradar/agents/graph.py`) with five pure passthrough stub nodes.
This ticket replaces the `triage_node` stub with the real zero-shot classifier that assigns a
filing's `domain` and an initial `risk_level`/`classification_confidence`.

A real Hugging Face API token is provisioned and live-verified against
`facebook/bart-large-mnli`. The ticket's own AI Coding Prompt cites a dead URL
(`api-inference.huggingface.co`, no longer resolves) — the verified working endpoint is
`https://router.huggingface.co/hf-inference/models/facebook/bart-large-mnli`, and the token needs
the "Make calls to Inference Providers" permission specifically.

**Cost/supervision constraint:** automated testing — including one real live HF call to verify
the integration — runs as part of building this ticket, same as every prior ticket's
live-verification pattern. What must not happen is leaving something running unsupervised
afterward that keeps calling the (billed, though effectively free-tier-covered) HF API on its
own — no persistent `docker-compose`/Prefect/Celery background stack. See
[[regradar-workflow-preferences]].

## Scope decisions (confirmed)

1. **Hosted HF API only for this ticket.** ADR-05 earmarks local `transformers`-based
   substitution (`USE_LOCAL_HF_INFERENCE`) for AGENT-02, but the user's actual concern was
   supervision, not needing $0 always — the free monthly credit already covers occasional
   live-verified test calls. Adding `torch`/`transformers` (a multi-GB dependency) is deferred
   until there's a concrete reason (e.g., running an eval suite hundreds of times).
2. **Graph nodes stay pure** (per AGENT-01's design) — `triage_node` only reads/writes
   `PipelineState`, no DB access. All DB persistence continues to happen in
   `workers/pipeline_tasks.py`'s `process_filing`, which already loads the `Filing` row and holds
   the DB session.
3. **No 100-filing accuracy gate in this ticket.** That labeled set doesn't exist yet, and EVAL-03
   is a separate, later ticket specifically chartered to build a 200-filing labeled
   precision/recall harness. AGENT-02 ships a small (~12–15 example) hand-labeled fixture set as a
   smoke test that `classify_filing()` behaves sensibly — not a rigorous accuracy benchmark — plus
   full unit-test coverage of the retry/heuristic/persistence logic via mocks.

## `src/regradar/agents/triage_agent.py` (new module)

```python
class TriageClassificationError(Exception):
    """Raised when HF classification fails after one retry."""

class ClassificationResult(BaseModel):
    domain: FilingDomain
    confidence: float
    raw_scores: dict[str, float]  # all 4 label scores — observability/debugging


def classify_filing(text: str) -> ClassificationResult:
    """Call HF's zero-shot classification endpoint for facebook/bart-large-mnli.

    POSTs to https://router.huggingface.co/hf-inference/models/facebook/bart-large-mnli
    with candidate_labels=["financial", "clinical", "environmental", "other"].
    Takes the top-scoring label as domain, its score as confidence.
    On an HTTP error or timeout: sleeps 2s, retries once. If the retry also
    fails, raises TriageClassificationError instead of guessing.
    Logs latency_ms, top label, and confidence on every call — feeds later
    cost/observability work (EVAL-06) without needing changes then.
    """


def derive_risk_level(domain: FilingDomain, confidence: float, text: str) -> RiskLevel:
    """Deterministic keyword + confidence heuristic for an initial risk_level.

    Rule set, in order:
      1. CRITICAL if `text` (case-insensitive) contains any critical-severity
         keyword: "material weakness", "restatement", "fraud", "going concern",
         "cease and desist", "consent decree", "delisting", "class action".
      2. HIGH if `text` contains any high-severity keyword: "deficiency",
         "non-compliance", "violation", "penalty", "recall", "warning letter",
         "sec investigation".
      3. MEDIUM if `confidence` < 0.5 — an uncertain classification is flagged
         for review, never silently treated as LOW.
      4. LOW otherwise (confident classification, no risk language detected).

    Keyword checks run before the confidence check, so a low-confidence but
    clearly severe filing is still flagged CRITICAL/HIGH, not downgraded to
    MEDIUM by the confidence rule.
    """


def triage_node(state: PipelineState) -> PipelineState:
    """The real triage node — replaces AGENT-01's passthrough stub.

    Calls classify_filing(state.raw_text). On success, sets state.domain,
    state.classification_confidence, and state.risk_level (via
    derive_risk_level) and returns state. On TriageClassificationError,
    returns state with domain/risk_level/classification_confidence left at
    their default None — process_filing (workers/pipeline_tasks.py) reads
    this as the signal to mark the filing needs_classification rather than
    guessing.
    """
```

`src/regradar/agents/graph.py` changes to a single import swap:
`from regradar.agents.triage_agent import triage_node` replaces the local stub function
definition. The function name stays `triage_node` so `build_graph()`'s node wiring is unchanged.

## Celery wiring (`src/regradar/workers/pipeline_tasks.py`)

`_run_pipeline_for_filing` persists the graph's classification result after `invoke()`:

```python
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

(Recall: `compiled_graph.invoke()` returns a plain `dict`, not a `PipelineState` — verified during
AGENT-01. `result["domain"]` etc. are dict-key lookups, not attribute access.)

## New DB enum value + migration

`FilingStatus.NEEDS_CLASSIFICATION = "needs_classification"` added to `models/enums.py`.

New migration `migrations/versions/0004_add_needs_classification_status.py`:
- `upgrade()`: `ALTER TYPE filing_status ADD VALUE 'needs_classification'` inside Alembic's
  `op.get_context().autocommit_block()` — Postgres requires `ALTER TYPE ... ADD VALUE` to run
  outside an explicit transaction.
- `downgrade()`: Postgres has no `ALTER TYPE ... DROP VALUE`. Standard workaround: defensively
  `UPDATE filings SET status = 'failed' WHERE status = 'needs_classification'`, then create a new
  `filing_status_old` enum type with the original 8 values, `ALTER TABLE filings ALTER COLUMN
  status TYPE filing_status_old USING status::text::filing_status_old`, `DROP TYPE filing_status`,
  `ALTER TYPE filing_status_old RENAME TO filing_status`.

## Tests

- `tests/unit/agents/test_triage_agent.py`:
  - `classify_filing`: mocked HTTP client — success on first call, success on retry after one
    failure, `TriageClassificationError` raised after both attempts fail.
  - `derive_risk_level`: one case per heuristic branch (critical keyword present, high keyword
    present, low confidence with no keywords, confident with no keywords), plus a case confirming
    a low-confidence-but-critical-keyword text still resolves to CRITICAL (keyword check runs
    first).
  - `triage_node`: success path (state populated with domain/risk_level/confidence) and failure
    path (`classify_filing` mocked to raise — state's classification fields stay `None`).
- `tests/unit/agents/fixtures/triage_smoke_set.json`: ~12–15 short realistic filing excerpts (one
  or more per domain) with hand-assigned expected `domain`.
- `tests/unit/agents/test_triage_live_smoke.py`: one test, marked `@pytest.mark.live`, that calls
  the real HF API against the fixture set and asserts a reasonable-not-rigorous accuracy bar
  (≥80% on this small set — deliberately looser than the ticket's 90%/100-filing target, which is
  EVAL-03's job with a real labeled set). Excluded from default `pytest` runs via a `live` marker
  registered in `pyproject.toml` with `addopts = "-m 'not live'"` (or the project's existing
  addopts, extended) — routine test runs, including CI, never hit the paid API. Run explicitly via
  `pytest -m live` when live verification is actually wanted.
- `tests/unit/workers/test_pipeline_tasks.py`: extend existing `process_filing` tests — one
  asserting the Filing row is updated with domain/risk_level/confidence/status=CLASSIFYING on a
  successful classification (graph's `triage_node` mocked at the module level, not a real HF
  call), one asserting status=NEEDS_CLASSIFICATION when classification fails.
