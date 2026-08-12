# AGENT-03 — Dual-Model Voting for Low-Confidence Classification

## Context

AGENT-02 (merged to `master`) built the real `triage_node` in `src/regradar/agents/triage_agent.py`:
`classify_filing()` calls HF's `facebook/bart-large-mnli` for zero-shot classification, `derive_risk_level()`
applies a keyword+confidence heuristic, and `triage_node()` orchestrates both. This ticket is a
Should-have safety net for PRD risk R5 (misclassification cascade): when the zero-shot classifier's
confidence is below a threshold, run a second, independent model as a spot-check, and take the
higher-severity risk_level if the two disagree.

## Model choice: local Ollama, not GPT-4o or Claude

The ticket's literal spec calls for "a second GPT-4o-based spot check." During design, provisioning
either a real OpenAI or a real Anthropic API key turned out to require paid credits the user
doesn't want to spend right now. The resolution: use a locally-hosted Ollama model (`llama3.1`,
already pulled — a 4.9GB download, verified working via a real local API call during design) via
its OpenAI-compatible endpoint, reusing the `openai` Python package (already a dependency) pointed
at `http://localhost:11434/v1` instead of the real OpenAI API.

This exercises config that ADR-05 already added but nothing has used yet: `USE_LOCAL_LLM`,
`LOCAL_LLM_BASE_URL` (default `http://localhost:11434/v1`), `LOCAL_LLM_MODEL` (default
`llama3.1`) in `core/config.py`. The code path for the real OpenAI API still exists (gated by
`USE_LOCAL_LLM=false`, using `TIER_HIGH_MODEL`/`OPENAI_API_KEY` unchanged) but is untested for now
— per this project's live-verification standard, untested paid-API code doesn't get to claim it
works. `.env` has `USE_LOCAL_LLM=true` set locally (an environment-specific override — the code
default in `config.py` stays `False`, matching ADR-05's documented "off by default" intent; a
developer opts in locally).

**Ollama itself is not left running in the background.** It's started manually only when a test
needs it (unit tests mock the client entirely and never touch it; the one live smoke test starts
it, runs, and the developer stops it afterward) — consistent with this project's policy against
unsupervised background processes making API-shaped calls.

## New config

`src/regradar/core/config.py` gains:

```python
classification_confidence_threshold: float = Field(
    default=0.75, alias="CLASSIFICATION_CONFIDENCE_THRESHOLD"
)
```

## `src/regradar/agents/triage_agent.py` additions

```python
SEVERITY_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}


class SpotCheckResult(BaseModel):
    domain: FilingDomain
    risk_level: RiskLevel
    reasoning: str


def _get_llm_client() -> tuple[OpenAI, str]:
    """Returns (client, model_name), routed to local Ollama or real OpenAI
    per settings.use_local_llm. Both branches use the same openai.OpenAI
    client class — Ollama's OpenAI-compatible endpoint means no separate
    SDK or code path is needed for the two cases."""


def spot_check_classification(text: str) -> SpotCheckResult | None:
    """Second-opinion classification + risk assessment for a low-confidence
    filing. A distinct prompt from classify_filing()'s — asks the model to
    independently classify into the same 4 domains AND assign its own
    risk_level with a short reasoning string, as JSON
    (response_format={"type": "json_object"}).

    Returns None (never raises) on any request, parse, or validation
    error — this is a safety-net enhancement, not a required step, and a
    spot-check failure must never break triage. Logs a warning on
    failure."""


def triage_node(state: PipelineState) -> PipelineState:
    """Extended: after a successful classify_filing() + derive_risk_level(),
    if result.confidence < settings.classification_confidence_threshold,
    calls spot_check_classification(state.raw_text). On a non-None result,
    logs both results in full (model names, domains, risk levels,
    confidence/reasoning, whether they agreed) via one structured
    logger.info() call, then sets the final risk_level to whichever of
    the HF heuristic's and the spot-check's risk_level is higher severity
    (SEVERITY_ORDER). domain always stays HF's — the ticket only asks for
    risk-level disagreement resolution, not domain reconciliation. If
    confidence is at or above the threshold, or the spot-check returns
    None, behavior is unchanged from AGENT-02."""
```

TriageClassificationError-raising cases from AGENT-02 (HF failure after retry) are unaffected —
if `classify_filing()` itself fails, `triage_node()` still returns early with everything `None`,
same as today; the spot-check never runs in that case (there is no confidence value to be low).

## Prompt shape

A distinct system+user prompt from the zero-shot call, e.g.:

> You are a regulatory filing classifier. Classify this filing into one of
> `["financial", "clinical", "environmental", "other"]`, and independently assign a risk_level
> (`"low"`, `"medium"`, `"high"`, `"critical"`) with a brief reasoning. Respond as JSON:
> `{"domain": ..., "risk_level": ..., "reasoning": ...}`.

Verified working against the real local `llama3.1` model during design — returned clean,
correctly-structured JSON with sensible classification and risk reasoning on a real test filing
excerpt.

## Tests

- `tests/unit/agents/test_triage_agent.py` (extended): `spot_check_classification` — mocked
  `openai.OpenAI` client covering a well-formed JSON response, a malformed/unparseable response
  (returns `None`, logs warning), and a request error (returns `None`). `triage_node` — extended
  with cases: confidence above threshold (spot-check never called, assert via mock
  `assert_not_called`), confidence below threshold with spot-check agreeing, confidence below
  threshold with spot-check disagreeing (higher severity wins in both directions — spot-check
  higher and HF heuristic higher), spot-check returning `None` (final risk_level stays the HF
  heuristic's, unaffected).
- `tests/unit/agents/test_triage_live_smoke.py` (extended): one new test,
  `@pytest.mark.live`, calling `spot_check_classification()` directly against the real local Ollama
  server with a real filing excerpt, asserting a well-formed `SpotCheckResult` comes back. Excluded
  from default runs by the existing `live` marker/`addopts` from AGENT-02; requires Ollama running
  locally (`USE_LOCAL_LLM=true` in `.env`) when run explicitly via `pytest -m live`.

## `.env.example` / config docs

Add `CLASSIFICATION_CONFIDENCE_THRESHOLD=0.75` to `.env.example` alongside the existing
`USE_LOCAL_LLM`/`LOCAL_LLM_BASE_URL`/`LOCAL_LLM_MODEL` entries (already present from ADR-05).
