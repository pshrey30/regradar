# AGENT-03 — Dual-Model Voting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When AGENT-02's HF zero-shot classifier returns a confidence below a configurable
threshold, run a second, independent spot-check via a local Ollama model and take the
higher-severity `risk_level` if the two disagree — a safety net against misclassification.

**Architecture:** All additions live in `src/regradar/agents/triage_agent.py`, extending the
module built in AGENT-02. A new `_get_llm_client()` helper returns an `openai.OpenAI` client
pointed at either local Ollama (`USE_LOCAL_LLM=true`, the current `.env` state) or the real OpenAI
API (`USE_LOCAL_LLM=false`, untested for now) — both branches use the same SDK class, since
Ollama exposes an OpenAI-compatible endpoint. `triage_node()` gains one new branch: below the
confidence threshold, call the spot-check and reconcile risk levels; unchanged otherwise.

**Tech Stack:** `openai` Python SDK (already a dependency, unused until this ticket) pointed at a
local Ollama server (`llama3.1`, already installed via Homebrew and pulled) instead of the real
OpenAI API.

## Global Constraints

- Design source of truth: `docs/superpowers/specs/2026-08-12-agent-03-dual-model-voting-design.md`.
- `domain` in the final state always comes from HF's zero-shot result — only `risk_level` is
  reconciled between the two models. Never overwrite `domain` with the spot-check's opinion.
- Spot-check only runs when `result.confidence < settings.classification_confidence_threshold`
  (default `0.75`). Normal-confidence classifications never make the extra call.
- `spot_check_classification()` never raises — returns `None` on any request, parse, or
  validation failure, logging a warning. A spot-check failure must never break triage.
- Severity comparison uses `SEVERITY_ORDER = {LOW: 0, MEDIUM: 1, HIGH: 2, CRITICAL: 3}` — the
  final `risk_level` is whichever of the HF heuristic's and the spot-check's is higher severity.
- `TriageClassificationError` (HF failure after retry) is unaffected — if `classify_filing()`
  itself fails, `triage_node()` still returns early with everything `None`; the spot-check never
  runs in that case.
- Automated tests must never call the real Ollama server by default — only the one test in
  `test_triage_live_smoke.py` marked `@pytest.mark.live` does, and only when explicitly run via
  `pytest -m live`.
- Do not run `brew services start ollama` or otherwise leave Ollama running persistently — start
  it manually only for the one live-verification step in this plan, then stop it.
- `USE_LOCAL_LLM=true` is already set in `.env` (a local environment override); the code default
  in `core/config.py` stays `False`, per ADR-05.

---

### Task 1: `classification_confidence_threshold` config

**Files:**
- Modify: `src/regradar/core/config.py`
- Modify: `.env.example`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `Settings.classification_confidence_threshold: float` (default `0.75`), for Task 3.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config.py`, inside `test_all_required_fields_present_loads_with_defaults`
(after the existing `assert settings.local_llm_model == "llama3.1"` line):

```python
    assert settings.classification_confidence_threshold == 0.75
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_config.py -v -k all_required_fields`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'classification_confidence_threshold'`

- [ ] **Step 3: Write the implementation**

In `src/regradar/core/config.py`, add this field in the `# ── Model routing tier thresholds ──`
section, after `tier_routing_risk_threshold`:

```python
    classification_confidence_threshold: float = Field(
        default=0.75, alias="CLASSIFICATION_CONFIDENCE_THRESHOLD"
    )
```

Add to `.env.example`, near the existing `TIER_HIGH_MODEL`/`TIER_LOW_MODEL`/
`TIER_ROUTING_RISK_THRESHOLD` lines:

```
CLASSIFICATION_CONFIDENCE_THRESHOLD=0.75
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_config.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/core/config.py .env.example tests/unit/test_config.py
git commit -m "Add classification_confidence_threshold config (AGENT-03)"
```

---

### Task 2: `SpotCheckResult` + `_get_llm_client` + `spot_check_classification`

**Files:**
- Modify: `src/regradar/agents/triage_agent.py`
- Test: `tests/unit/agents/test_triage_agent.py`

**Interfaces:**
- Consumes: `Settings.classification_confidence_threshold`, `Settings.use_local_llm`,
  `Settings.local_llm_base_url`, `Settings.local_llm_model`, `Settings.tier_high_model`,
  `Settings.openai_api_key` (all pre-existing in `core/config.py`).
- Produces (for Task 3):
  - `SEVERITY_ORDER: dict[RiskLevel, int]`
  - `class SpotCheckResult(BaseModel)`: fields `domain: FilingDomain`, `risk_level: RiskLevel`,
    `reasoning: str`.
  - `def _get_llm_client() -> tuple[OpenAI, str]`
  - `def spot_check_classification(text: str) -> SpotCheckResult | None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/agents/test_triage_agent.py`, after the existing imports (extend the import
block rather than adding a second one):

```python
from unittest.mock import ANY

from regradar.agents.triage_agent import SpotCheckResult, spot_check_classification
```

Then append these tests (after the `derive_risk_level` tests, before the `_make_state` /
`triage_node` tests):

```python
def _mock_openai_client(content: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    client.chat.completions.create.return_value = response
    return client


def test_spot_check_classification_returns_parsed_result() -> None:
    content = (
        '{"domain": "financial", "risk_level": "high", '
        '"reasoning": "Material weakness disclosed."}'
    )
    with patch(
        "regradar.agents.triage_agent._get_llm_client",
        return_value=(_mock_openai_client(content), "llama3.1"),
    ):
        result = spot_check_classification("some filing text")

    assert result == SpotCheckResult(
        domain=FilingDomain.FINANCIAL,
        risk_level=RiskLevel.HIGH,
        reasoning="Material weakness disclosed.",
    )


def test_spot_check_classification_returns_none_on_malformed_json() -> None:
    with patch(
        "regradar.agents.triage_agent._get_llm_client",
        return_value=(_mock_openai_client("not valid json"), "llama3.1"),
    ):
        result = spot_check_classification("some filing text")

    assert result is None


def test_spot_check_classification_returns_none_on_request_error() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("connection refused")
    with patch("regradar.agents.triage_agent._get_llm_client", return_value=(client, "llama3.1")):
        result = spot_check_classification("some filing text")

    assert result is None


def test_get_llm_client_uses_local_settings_when_use_local_llm_true() -> None:
    fake_settings = MagicMock()
    fake_settings.use_local_llm = True
    fake_settings.local_llm_base_url = "http://localhost:11434/v1"
    fake_settings.local_llm_model = "llama3.1"

    with patch("regradar.agents.triage_agent.get_settings", return_value=fake_settings):
        with patch("regradar.agents.triage_agent.OpenAI") as mock_openai_cls:
            client, model = _get_llm_client()

    mock_openai_cls.assert_called_once_with(base_url="http://localhost:11434/v1", api_key=ANY)
    assert model == "llama3.1"


def test_get_llm_client_uses_real_openai_when_use_local_llm_false() -> None:
    fake_settings = MagicMock()
    fake_settings.use_local_llm = False
    fake_settings.tier_high_model = "gpt-4o"
    fake_settings.openai_api_key.get_secret_value.return_value = "sk-real"

    with patch("regradar.agents.triage_agent.get_settings", return_value=fake_settings):
        with patch("regradar.agents.triage_agent.OpenAI") as mock_openai_cls:
            client, model = _get_llm_client()

    mock_openai_cls.assert_called_once_with(api_key="sk-real")
    assert model == "gpt-4o"
```

Add the missing import for `_get_llm_client` at the top of the test file too:

```python
from regradar.agents.triage_agent import _get_llm_client
```

(add this alongside the other `from regradar.agents.triage_agent import (...)` block, or as a
separate import line — either works)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/agents/test_triage_agent.py -v -k "spot_check or get_llm_client"`
Expected: FAIL with `ImportError: cannot import name 'SpotCheckResult' from 'regradar.agents.triage_agent'`

- [ ] **Step 3: Write the implementation**

In `src/regradar/agents/triage_agent.py`, add to the imports at the top:

```python
import json

from openai import OpenAI
```

(add `import json` and `from openai import OpenAI` alongside the existing `import logging`,
`import time`, `import httpx`, `from pydantic import BaseModel` block)

Add after the existing `LOW_CONFIDENCE_THRESHOLD = 0.5` line:

```python
SEVERITY_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

SPOT_CHECK_SYSTEM_PROMPT = "You are a regulatory filing classifier. Respond with strict JSON only."
SPOT_CHECK_USER_PROMPT_TEMPLATE = (
    'Classify this filing into one of ["financial", "clinical", "environmental", "other"], '
    'and independently assign a risk_level ("low", "medium", "high", "critical") with a brief '
    'reasoning. Text: "{text}" '
    'Respond as JSON: {{"domain": ..., "risk_level": ..., "reasoning": ...}}'
)
```

Add after the `ClassificationResult` class definition:

```python
class SpotCheckResult(BaseModel):
    domain: FilingDomain
    risk_level: RiskLevel
    reasoning: str
```

Add after `classify_filing` and before `derive_risk_level`:

```python
def _get_llm_client() -> tuple[OpenAI, str]:
    """Returns (client, model_name), routed to local Ollama or real OpenAI
    per settings.use_local_llm. Both branches use the same OpenAI SDK
    class — Ollama's OpenAI-compatible endpoint means no separate client
    or code path is needed for the two cases.
    """
    settings = get_settings()
    if settings.use_local_llm:
        return (
            OpenAI(base_url=settings.local_llm_base_url, api_key="ollama-local"),
            settings.local_llm_model,
        )
    return OpenAI(api_key=settings.openai_api_key.get_secret_value()), settings.tier_high_model


def spot_check_classification(text: str) -> SpotCheckResult | None:
    """Second-opinion classification + risk assessment for a low-confidence
    filing. Never raises — returns None on any request, parse, or
    validation error, since this is a safety-net enhancement and a
    spot-check failure must never break triage.
    """
    client, model = _get_llm_client()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SPOT_CHECK_SYSTEM_PROMPT},
                {"role": "user", "content": SPOT_CHECK_USER_PROMPT_TEMPLATE.format(text=text)},
            ],
            temperature=0,
        )
        content = response.choices[0].message.content
        parsed = json.loads(content)
        return SpotCheckResult(
            domain=FilingDomain(parsed["domain"]),
            risk_level=RiskLevel(parsed["risk_level"]),
            reasoning=parsed["reasoning"],
        )
    except Exception as exc:  # noqa: BLE001 — any failure here must degrade, not raise
        logger.warning("Spot-check classification failed: %s", exc)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/agents/test_triage_agent.py -v`
Expected: PASS (all tests in the file, including the pre-existing ones from AGENT-02)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/agents/triage_agent.py tests/unit/agents/test_triage_agent.py
git commit -m "Add GPT-4o/local-Ollama spot-check classification call (AGENT-03)"
```

---

### Task 3: Wire the spot-check into `triage_node`

**Files:**
- Modify: `src/regradar/agents/triage_agent.py`
- Test: `tests/unit/agents/test_triage_agent.py`

**Interfaces:**
- Consumes: `spot_check_classification`, `SEVERITY_ORDER`, `SpotCheckResult` (Task 2).
- Produces: updated `triage_node` — same signature, extended behavior.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/agents/test_triage_agent.py`, after the existing `triage_node` tests:

```python
def test_triage_node_skips_spot_check_above_confidence_threshold() -> None:
    fake_result = ClassificationResult(
        domain=FilingDomain.FINANCIAL, confidence=0.9, raw_scores={"financial": 0.9}
    )
    with patch("regradar.agents.triage_agent.classify_filing", return_value=fake_result):
        with patch("regradar.agents.triage_agent.spot_check_classification") as mock_spot_check:
            triage_node(_make_state())

    mock_spot_check.assert_not_called()


def test_triage_node_upgrades_risk_level_when_spot_check_disagrees_higher() -> None:
    fake_result = ClassificationResult(
        domain=FilingDomain.FINANCIAL, confidence=0.4, raw_scores={"financial": 0.4}
    )
    spot_result = SpotCheckResult(
        domain=FilingDomain.FINANCIAL, risk_level=RiskLevel.CRITICAL, reasoning="Fraud indicators."
    )
    with patch("regradar.agents.triage_agent.classify_filing", return_value=fake_result):
        with patch(
            "regradar.agents.triage_agent.spot_check_classification", return_value=spot_result
        ):
            state = triage_node(_make_state())

    # Base heuristic on 0.4 confidence + no keywords in "Routine filing text." is MEDIUM;
    # spot-check says CRITICAL — higher severity wins.
    assert state.risk_level == RiskLevel.CRITICAL
    assert state.domain == FilingDomain.FINANCIAL  # domain always stays HF's


def test_triage_node_keeps_own_risk_level_when_spot_check_agrees_lower() -> None:
    fake_result = ClassificationResult(
        domain=FilingDomain.FINANCIAL, confidence=0.4, raw_scores={"financial": 0.4}
    )
    spot_result = SpotCheckResult(
        domain=FilingDomain.FINANCIAL, risk_level=RiskLevel.LOW, reasoning="Looks routine."
    )
    with patch("regradar.agents.triage_agent.classify_filing", return_value=fake_result):
        with patch(
            "regradar.agents.triage_agent.spot_check_classification", return_value=spot_result
        ):
            state = triage_node(_make_state())

    # Base heuristic is MEDIUM (low confidence, no keywords); spot-check says LOW —
    # MEDIUM is higher severity, so it wins.
    assert state.risk_level == RiskLevel.MEDIUM


def test_triage_node_unaffected_when_spot_check_returns_none() -> None:
    fake_result = ClassificationResult(
        domain=FilingDomain.FINANCIAL, confidence=0.4, raw_scores={"financial": 0.4}
    )
    with patch("regradar.agents.triage_agent.classify_filing", return_value=fake_result):
        with patch("regradar.agents.triage_agent.spot_check_classification", return_value=None):
            state = triage_node(_make_state())

    assert state.risk_level == RiskLevel.MEDIUM
    assert state.domain == FilingDomain.FINANCIAL
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/agents/test_triage_agent.py -v -k triage_node_skips_spot_check`
Expected: FAIL — `mock_spot_check.assert_not_called()` fails because `triage_node` doesn't call
`spot_check_classification` at all yet (import doesn't exist in `triage_node`'s scope, so the
patch target exists but is never invoked either way — the disagreement tests are the ones that
actually demonstrate the missing behavior: they'll FAIL because `state.risk_level` stays at the
un-reconciled heuristic value while the test expects the spot-check's influence).

Run: `.venv/bin/pytest tests/unit/agents/test_triage_agent.py -v -k triage_node_upgrades_risk_level`
Expected: FAIL — `assert state.risk_level == RiskLevel.CRITICAL` fails, actual is `RiskLevel.MEDIUM`
(the un-reconciled heuristic result)

- [ ] **Step 3: Write the implementation**

Replace `triage_node` in `src/regradar/agents/triage_agent.py`:

```python
def triage_node(state: PipelineState) -> PipelineState:
    """The real triage node — replaces AGENT-01's passthrough stub.

    On success, sets state.domain, state.classification_confidence, and
    state.risk_level. On TriageClassificationError, leaves those three
    fields at their default None — workers/pipeline_tasks.py reads this
    as the signal to mark the filing needs_classification instead of
    guessing.

    If the HF classification's confidence is below
    settings.classification_confidence_threshold (AGENT-03), a second,
    independent spot-check model classifies the same filing and assigns
    its own risk_level opinion. If the spot-check succeeds, the final
    risk_level is whichever of the two is higher severity — domain
    always stays HF's, only risk_level is reconciled. A spot-check
    failure (returns None) leaves the HF-heuristic risk_level unchanged.
    """
    try:
        result = classify_filing(state.raw_text)
    except TriageClassificationError:
        logger.error(
            "Triage classification failed for filing %s; leaving unclassified", state.filing_id
        )
        return state

    risk_level = derive_risk_level(result.domain, result.confidence, state.raw_text)

    settings = get_settings()
    if result.confidence < settings.classification_confidence_threshold:
        spot_result = spot_check_classification(state.raw_text)
        if spot_result is not None:
            agreed = spot_result.risk_level == risk_level
            logger.info(
                "Dual-model vote for filing %s: hf_domain=%s hf_risk=%s hf_confidence=%.3f "
                "spot_domain=%s spot_risk=%s spot_reasoning=%r agreed=%s",
                state.filing_id,
                result.domain,
                risk_level,
                result.confidence,
                spot_result.domain,
                spot_result.risk_level,
                spot_result.reasoning,
                agreed,
            )
            if SEVERITY_ORDER[spot_result.risk_level] > SEVERITY_ORDER[risk_level]:
                risk_level = spot_result.risk_level

    return state.model_copy(
        update={
            "domain": result.domain,
            "classification_confidence": result.confidence,
            "risk_level": risk_level,
        }
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/agents/test_triage_agent.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/agents/triage_agent.py tests/unit/agents/test_triage_agent.py
git commit -m "Wire dual-model voting into triage_node (AGENT-03)"
```

---

### Task 4: Live smoke test against real local Ollama

**Files:**
- Modify: `tests/unit/agents/test_triage_live_smoke.py`

**Interfaces:**
- Consumes: `spot_check_classification` (Task 2).
- Produces: nothing consumed by later tasks — final verification.

- [ ] **Step 1: Write the test**

Append to `tests/unit/agents/test_triage_live_smoke.py`:

```python
from regradar.agents.triage_agent import spot_check_classification


@pytest.mark.live
def test_spot_check_classification_live_smoke_test() -> None:
    """Requires Ollama running locally with llama3.1 pulled, and
    USE_LOCAL_LLM=true in .env (both already true in this repo's local
    setup). Start Ollama manually before running this test explicitly
    (`ollama serve`, or the background-launch pattern used during
    AGENT-03's design verification) — do not leave it running afterward.
    """
    result = spot_check_classification(
        "The company disclosed a material weakness in internal controls "
        "over financial reporting and must remediate within 90 days."
    )

    assert result is not None
    assert result.domain.value in ("financial", "clinical", "environmental", "other")
    assert result.risk_level.value in ("low", "medium", "high", "critical")
    assert len(result.reasoning) > 0
```

- [ ] **Step 2: Start Ollama manually for this one verification**

Run: `/opt/homebrew/opt/ollama/bin/ollama serve > /tmp/ollama-serve.log 2>&1 &` then wait a couple
seconds and confirm it's up with `curl -s http://localhost:11434`.

- [ ] **Step 3: Run the live test explicitly**

Run: `.venv/bin/pytest tests/unit/agents/test_triage_live_smoke.py -v -m live`
Expected: PASS — both live tests in this file run (the existing HF one from AGENT-02 and this new
Ollama one), confirming the real dual-model integration works end to end.

- [ ] **Step 4: Stop Ollama**

Find and kill the `ollama serve` process started in Step 2 (e.g. `pkill -f "ollama serve"`), then
confirm it's down with `curl -s http://localhost:11434` (should fail to connect). Do not leave it
running.

- [ ] **Step 5: Run the default suite to confirm the live tests stay excluded**

Run: `.venv/bin/pytest tests/unit/agents -v`
Expected: both `live`-marked tests are deselected; all non-live tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/agents/test_triage_live_smoke.py
git commit -m "Add live smoke test for the Ollama spot-check (AGENT-03)"
```

---

### Task 5: Full-suite check, lint, mypy, push

**Files:** none (verification only)

- [ ] **Step 1: Run the full default test suite**

Run: `.venv/bin/pytest -v`
Expected: PASS (live-marked tests excluded by default; the two pre-existing
`tests/integration/test_flows.py` tests may still error locally if Postgres isn't running — a
pre-existing condition unrelated to this branch, confirmed during AGENT-01/02).

- [ ] **Step 2: Run lint and type checks**

Run: `.venv/bin/ruff check src/regradar/agents src/regradar/core/config.py tests/unit/agents tests/unit/test_config.py`
Run: `.venv/bin/mypy src/regradar/agents src/regradar/core/config.py`
Expected: no errors. Fix any and commit the fix as part of this task.

- [ ] **Step 3: Push the branch**

```bash
git push -u origin agent-03-dual-model-voting
```

Do not merge to `master` — per project convention, merging is a separate explicit step the user
confirms.
