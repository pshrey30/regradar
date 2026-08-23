# AGENT-09 — Tiered Model Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/regradar/llm_routing/tiered_router.py` — a single `select_model(risk_level, task) -> ModelChoice` function every LLM-calling agent uses to pick between a "high" tier model (Critical/High risk) and a cheaper "low" tier model (Low/Medium risk), per a configurable risk threshold — and wire it into both existing LLM-calling agents (`analysis_agent.py`, `summarization_agent.py`), replacing their private `_get_llm_client()` duplicates. Add a circuit-breaker-style fallback: if the selected tier's provider raises a connection/rate-limit error, retry once against the other tier.

**Architecture:** `tiered_router.py` exposes a pure-decision `ModelChoice` Pydantic model plus three functions: `select_model_for_tier(tier, task)` (the actual provider-selection logic, mode-aware via `settings.use_local_llm`), `select_model(risk_level, task)` (risk → tier → `select_model_for_tier`), and `other_tier_choice(choice, task)` (used by the fallback path). `build_client(choice)` constructs the `OpenAI`-compatible client. Both agent files keep a thin per-file `_get_llm_client(risk_level)` wrapper (mirroring the existing per-file convention established in AGENT-07/08, where each agent owns its retry/validation logic) that now delegates tier *selection* to the shared router instead of duplicating it, and each agent's existing retry loop gains one new except-clause that catches `openai.APIConnectionError`/`openai.RateLimitError`, swaps to the other tier exactly once via `other_tier_choice`, and continues.

**Local-tier deviation (confirmed with user before planning):** the ticket's literal wording assumes GPT-4o (high) vs. Granite-13B-via-HF (low) as real providers. This project runs entirely on free local Ollama in dev (`USE_LOCAL_LLM=true`, per ADR-05) — until now, on a single model (`llama3.1`) for everything, so there was no real "cheap tier" to route to locally. Per explicit user decision, this ticket pulls `llama3.2:1b` (~1.3GB, free, permanent, zero ongoing cost) as the genuine local low-tier model, adding `LOCAL_LLM_LOW_MODEL` config. This lets tiered routing be live-verified end-to-end with two real, distinct local models — consistent with this project's real-live-verification bar — rather than only existing as mocked-test logic. The real (non-local) OpenAI-high/HF-Granite-low path is implemented per the ticket's literal spec but, like AGENT-03's GPT-4o spot-check, stays **untested/unexercised against a real provider** (no OpenAI/HF chat-completions credits provisioned) — verified only via mocked unit tests, not live calls.

**Tech Stack:** Python 3.11, Pydantic v2, `openai` SDK, pytest + `unittest.mock`.

## Global Constraints

- `select_model`'s tier decision must be config-driven, not hardcoded: `settings.tier_routing_risk_threshold` (default `"high"`) is the cutoff — any risk level at or above it routes to the high tier.
- `RiskLevel` (`src/regradar/models/enums.py`) has no built-in ordering — build an explicit ordinal map `{LOW: 0, MEDIUM: 1, HIGH: 2, CRITICAL: 3}` in the router; do not rely on enum declaration order or string comparison.
- An unclassified filing (`risk_level=None`, e.g. `analyze_node` running before triage successfully set it) must default to the **high** tier — mirrors the existing precedent in `agents/graph.py`'s `route_after_triage`, which treats `risk_level=None` the same as any non-low risk rather than defaulting to the cheaper/faster path for an unclassified filing.
- Both agent files' existing validation retry loop (JSON/schema malformed-response handling, `MAX_ATTEMPTS=2`) is unchanged — the new connection/rate-limit fallback is a *separate*, additional except-clause in the same loop, not a restructuring of the existing one.
- The fallback swap happens **at most once** per node invocation (track via a local `used_fallback` boolean) — never loop indefinitely between tiers.
- No new Alembic migration — this ticket only adds a Pydantic Settings field (`local_llm_low_model`), not a DB column.
- Reuse the existing `OpenAI(base_url=..., api_key=...)` client-construction pattern already used in both agent files — `ModelChoice` must carry enough info (`base_url`, `api_key`, `model`) for `build_client()` to construct a working client without any agent hardcoding provider details itself.

---

## Task 1: `llm_routing/tiered_router.py` core + config field + unit tests

**Files:**
- Modify: `src/regradar/core/config.py`
- Create: `src/regradar/llm_routing/tiered_router.py`
- Create: `tests/unit/llm_routing/__init__.py`
- Create: `tests/unit/llm_routing/test_tiered_router.py`

**Interfaces:**
- Consumes: `get_settings()` from `regradar.core.config` (existing fields: `use_local_llm: bool`, `local_llm_base_url: str`, `local_llm_model: str`, `tier_high_model: str`, `tier_low_model: str`, `tier_routing_risk_threshold: str`, `openai_api_key: SecretStr`, `huggingface_api_token: SecretStr`; new field this task adds: `local_llm_low_model: str`); `RiskLevel` from `regradar.models.enums` (members: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`, each a `str` value e.g. `"low"`).
- Produces: `ModelChoice` (Pydantic model: `tier: Literal["high", "low"]`, `model: str`, `base_url: str | None`, `api_key: str`), `select_model(risk_level: RiskLevel | None, task: Literal["analysis", "summarization"]) -> ModelChoice`, `select_model_for_tier(tier: Literal["high", "low"], task: Literal["analysis", "summarization"]) -> ModelChoice`, `other_tier_choice(choice: ModelChoice, task: Literal["analysis", "summarization"]) -> ModelChoice`, `build_client(choice: ModelChoice) -> OpenAI` — all consumed by Tasks 2 and 3.

- [ ] **Step 1: Add the new config field**

In `src/regradar/core/config.py`, find the existing local-inference block:

```python
    use_local_llm: bool = Field(default=False, alias="USE_LOCAL_LLM")
    local_llm_base_url: str = Field(
        default="http://localhost:11434/v1", alias="LOCAL_LLM_BASE_URL"
    )
    local_llm_model: str = Field(default="llama3.1", alias="LOCAL_LLM_MODEL")
```

Add a new field immediately after `local_llm_model`:

```python
    local_llm_low_model: str = Field(default="llama3.2:1b", alias="LOCAL_LLM_LOW_MODEL")
```

Also add `LOCAL_LLM_LOW_MODEL=` to the repo's `.env.example` (find the existing `LOCAL_LLM_MODEL=` line and add `LOCAL_LLM_LOW_MODEL=` right after it, with a placeholder/empty value matching that file's existing style for optional fields with defaults).

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/llm_routing/__init__.py`:

```python
"""Unit tests for the tiered model routing package."""
```

Create `tests/unit/llm_routing/test_tiered_router.py`:

```python
"""Unit tests for select_model's risk-tier decision and provider wiring."""

import os

os.environ.setdefault("APP_SECRET_KEY", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_BUCKET_NAME", "test-bucket")
os.environ.setdefault("S3_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("HUGGINGFACE_API_TOKEN", "test-hf-token")
os.environ.setdefault("SEC_EDGAR_USER_AGENT", "RegRadar/1.0 (test@example.com)")

from unittest.mock import patch

import pytest
from openai import OpenAI

from regradar.core.config import get_settings
from regradar.llm_routing.tiered_router import (
    ModelChoice,
    build_client,
    other_tier_choice,
    select_model,
    select_model_for_tier,
)
from regradar.models.enums import RiskLevel


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("risk_level", [RiskLevel.LOW, RiskLevel.MEDIUM])
def test_select_model_routes_low_and_medium_to_low_tier(
    risk_level: RiskLevel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USE_LOCAL_LLM", "false")
    choice = select_model(risk_level, task="analysis")
    assert choice.tier == "low"


@pytest.mark.parametrize("risk_level", [RiskLevel.HIGH, RiskLevel.CRITICAL])
def test_select_model_routes_high_and_critical_to_high_tier(
    risk_level: RiskLevel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USE_LOCAL_LLM", "false")
    choice = select_model(risk_level, task="analysis")
    assert choice.tier == "high"


def test_select_model_with_none_risk_level_defaults_to_high_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USE_LOCAL_LLM", "false")
    choice = select_model(None, task="analysis")
    assert choice.tier == "high"


def test_select_model_respects_custom_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USE_LOCAL_LLM", "false")
    monkeypatch.setenv("TIER_ROUTING_RISK_THRESHOLD", "critical")
    choice = select_model(RiskLevel.HIGH, task="analysis")
    assert choice.tier == "low"
    choice_critical = select_model(RiskLevel.CRITICAL, task="analysis")
    assert choice_critical.tier == "high"


def test_select_model_for_tier_local_mode_uses_local_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USE_LOCAL_LLM", "true")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "llama3.1")
    monkeypatch.setenv("LOCAL_LLM_LOW_MODEL", "llama3.2:1b")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")

    high_choice = select_model_for_tier("high", task="analysis")
    assert high_choice.model == "llama3.1"
    assert high_choice.base_url == "http://localhost:11434/v1"
    assert high_choice.api_key == "ollama-local"

    low_choice = select_model_for_tier("low", task="analysis")
    assert low_choice.model == "llama3.2:1b"
    assert low_choice.base_url == "http://localhost:11434/v1"


def test_select_model_for_tier_real_mode_uses_openai_and_hf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USE_LOCAL_LLM", "false")
    monkeypatch.setenv("TIER_HIGH_MODEL", "gpt-4o")
    monkeypatch.setenv("TIER_LOW_MODEL", "granite-13b")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "hf-test-token")

    high_choice = select_model_for_tier("high", task="analysis")
    assert high_choice.model == "gpt-4o"
    assert high_choice.base_url is None
    assert high_choice.api_key == "sk-test-openai"

    low_choice = select_model_for_tier("low", task="analysis")
    assert low_choice.model == "granite-13b"
    assert low_choice.base_url == "https://router.huggingface.co/v1"
    assert low_choice.api_key == "hf-test-token"


def test_other_tier_choice_inverts_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USE_LOCAL_LLM", "true")
    high_choice = select_model_for_tier("high", task="analysis")
    fallback = other_tier_choice(high_choice, task="analysis")
    assert fallback.tier == "low"

    low_choice = select_model_for_tier("low", task="analysis")
    fallback_from_low = other_tier_choice(low_choice, task="analysis")
    assert fallback_from_low.tier == "high"


def test_build_client_constructs_openai_client_with_choice_settings() -> None:
    choice = ModelChoice(
        tier="high", model="llama3.1", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )
    client = build_client(choice)
    assert isinstance(client, OpenAI)
    assert str(client.base_url).rstrip("/") == "http://localhost:11434/v1"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/llm_routing/test_tiered_router.py -v`
Expected: `ModuleNotFoundError: No module named 'regradar.llm_routing.tiered_router'` (or `ImportError`) for every test.

- [ ] **Step 4: Write the implementation**

Create `src/regradar/llm_routing/tiered_router.py`:

```python
"""Tiered model routing — a single decision point every LLM-calling agent
uses to pick between the "high" tier (GPT-4o, or local llama3.1) and the
cheaper "low" tier (HF-hosted Granite-13B, or local llama3.2:1b), based on
a filing's risk_level.

Deviates from the ticket's literal GPT-4o/Granite-13B wording for the
LOCAL_LLM mode: this project runs entirely on free local Ollama in dev
(ADR-05), so the "low" tier here routes to a second, genuinely smaller
local model (llama3.2:1b) rather than collapsing to the same model as the
high tier — chosen explicitly so tiered routing can be live-verified with
two real, distinct outputs at $0 cost, per user decision. The real
OpenAI-high/HF-Granite-low path is implemented per the ticket's literal
spec but — like AGENT-03's GPT-4o spot check — is untested against a real
provider (no OpenAI/HF chat-completions credits provisioned); only the
local-mode path and mocked unit tests exercise this code.
"""

from typing import Literal

from openai import OpenAI
from pydantic import BaseModel

from regradar.core.config import get_settings
from regradar.models.enums import RiskLevel

_RISK_ORDER: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

Tier = Literal["high", "low"]
Task = Literal["analysis", "summarization"]


class ModelChoice(BaseModel):
    """Everything a caller needs to build a working OpenAI-compatible client.

    `task` isn't part of this model — today's routing decision doesn't
    differ by task (analysis and summarization use the same tier/model for
    a given risk level), but `select_model`/`select_model_for_tier` accept
    a `task` parameter per the ticket's literal interface, for future
    per-task tier differentiation (e.g. a smaller model for summarization
    than analysis at the same risk level) without a signature change.
    """

    tier: Tier
    model: str
    base_url: str | None = None
    api_key: str


def _tier_for_risk(risk_level: RiskLevel | None) -> Tier:
    """Unclassified (risk_level=None) defaults to the high tier — mirrors
    agents/graph.py's route_after_triage, which treats an unclassified
    filing the same as any non-low risk rather than defaulting to the
    cheaper path for a filing we haven't actually assessed yet."""
    settings = get_settings()
    threshold = RiskLevel(settings.tier_routing_risk_threshold)
    effective_risk = risk_level or RiskLevel.HIGH
    return "high" if _RISK_ORDER[effective_risk] >= _RISK_ORDER[threshold] else "low"


def select_model_for_tier(tier: Tier, task: Task) -> ModelChoice:
    """The actual provider/model selection for an already-decided tier."""
    settings = get_settings()
    if settings.use_local_llm:
        model = settings.local_llm_model if tier == "high" else settings.local_llm_low_model
        return ModelChoice(
            tier=tier, model=model, base_url=settings.local_llm_base_url, api_key="ollama-local"
        )
    if tier == "high":
        return ModelChoice(
            tier="high",
            model=settings.tier_high_model,
            base_url=None,
            api_key=settings.openai_api_key.get_secret_value(),
        )
    return ModelChoice(
        tier="low",
        model=settings.tier_low_model,
        base_url="https://router.huggingface.co/v1",
        api_key=settings.huggingface_api_token.get_secret_value(),
    )


def select_model(risk_level: RiskLevel | None, task: Task) -> ModelChoice:
    """Risk level -> tier -> provider/model. The one entry point agents use."""
    return select_model_for_tier(_tier_for_risk(risk_level), task)


def other_tier_choice(choice: ModelChoice, task: Task) -> ModelChoice:
    """The opposite tier's ModelChoice for the same task — used by the
    circuit-breaker fallback when the primary tier's provider fails."""
    other: Tier = "low" if choice.tier == "high" else "high"
    return select_model_for_tier(other, task)


def build_client(choice: ModelChoice) -> OpenAI:
    return OpenAI(base_url=choice.base_url, api_key=choice.api_key)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/llm_routing/test_tiered_router.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 6: Run the full unit test suite to check for regressions**

Run: `pytest tests/unit -v`
Expected: all tests PASS (no regressions in unrelated modules — the new config field has a default, so nothing that constructs `Settings()` without it should break).

- [ ] **Step 7: Commit**

```bash
git add src/regradar/core/config.py .env.example src/regradar/llm_routing/tiered_router.py tests/unit/llm_routing/__init__.py tests/unit/llm_routing/test_tiered_router.py
git commit -m "Add tiered model routing core (AGENT-09)"
```

---

## Task 2: Wire tiered routing into the Analysis Agent

**Files:**
- Modify: `src/regradar/agents/analysis_agent.py`
- Modify: `tests/unit/agents/test_analysis_agent.py`

**Interfaces:**
- Consumes: `select_model`, `other_tier_choice`, `build_client`, `ModelChoice` from `regradar.llm_routing.tiered_router` (produced by Task 1); `openai.APIConnectionError`, `openai.RateLimitError` (from the `openai` package, already a project dependency).
- Produces: `analyze_node` now routes model selection through the shared router based on `state.risk_level`, and falls back to the other tier once on a connection/rate-limit error. `_get_llm_client` in this file changes signature from `() -> tuple[OpenAI, str]` to `(risk_level: RiskLevel | None) -> tuple[OpenAI, str, ModelChoice]` — existing tests patching this function must update their mocked return value from a 2-tuple to a 3-tuple.

- [ ] **Step 1: Update the existing tests for the new `_get_llm_client` signature**

In `tests/unit/agents/test_analysis_agent.py`, every `patch("regradar.agents.analysis_agent._get_llm_client", return_value=(...))` currently mocks a 2-tuple `(mock_client, "llama3.1")`. Update every one of these to a 3-tuple, adding a `ModelChoice` as the third element. Add this import near the top of the file (alongside the existing imports):

```python
from regradar.llm_routing.tiered_router import ModelChoice
```

And this helper near the other test helpers:

```python
def _fake_model_choice(model: str = "llama3.1") -> ModelChoice:
    return ModelChoice(tier="high", model=model, base_url="http://localhost:11434/v1", api_key="ollama-local")
```

Then change every occurrence of `return_value=(_mock_openai_client(content), "llama3.1")` to `return_value=(_mock_openai_client(content), "llama3.1", _fake_model_choice())`, and every occurrence of `return_value=(client, "llama3.1")` similarly to `return_value=(client, "llama3.1", _fake_model_choice())`. There are 6 such patch call sites in the file (one per existing test) — update all of them. The one exception is `test_analyze_node_with_no_chunks_leaves_extraction_none`, which asserts `mock_get_client.assert_not_called()` and does not set a `return_value` — leave that one as-is.

- [ ] **Step 2: Write the new failing tests for risk-based routing and fallback**

Add these tests to `tests/unit/agents/test_analysis_agent.py` (after the existing tests):

```python
from openai import APIConnectionError

from regradar.llm_routing.tiered_router import ModelChoice
from regradar.models.enums import RiskLevel


def test_analyze_node_passes_state_risk_level_to_get_llm_client() -> None:
    content = json.dumps(VALID_EXTRACTION_JSON)
    state = _make_state_with_chunks()
    state = state.model_copy(update={"risk_level": RiskLevel.CRITICAL})

    with patch(
        "regradar.agents.analysis_agent._get_llm_client",
        return_value=(_mock_openai_client(content), "llama3.1", _fake_model_choice()),
    ) as mock_get_client:
        analyze_node(state)

    mock_get_client.assert_called_once_with(RiskLevel.CRITICAL)


def test_analyze_node_falls_back_to_other_tier_on_connection_error() -> None:
    content = json.dumps(VALID_EXTRACTION_JSON)
    primary_client = MagicMock()
    primary_client.chat.completions.create.side_effect = APIConnectionError(
        request=MagicMock()
    )
    fallback_client = _mock_openai_client(content)

    primary_choice = ModelChoice(
        tier="high", model="llama3.1", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )
    fallback_choice = ModelChoice(
        tier="low", model="llama3.2:1b", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )

    with (
        patch(
            "regradar.agents.analysis_agent._get_llm_client",
            return_value=(primary_client, "llama3.1", primary_choice),
        ),
        patch(
            "regradar.agents.analysis_agent.other_tier_choice",
            return_value=fallback_choice,
        ),
        patch(
            "regradar.agents.analysis_agent.build_client",
            return_value=fallback_client,
        ),
    ):
        result = analyze_node(_make_state_with_chunks())

    assert result.extraction is not None
    assert result.extraction.model_used == "llama3.2:1b"


def test_analyze_node_gives_up_after_fallback_also_fails() -> None:
    primary_client = MagicMock()
    primary_client.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())
    fallback_client = MagicMock()
    fallback_client.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())

    primary_choice = ModelChoice(
        tier="high", model="llama3.1", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )
    fallback_choice = ModelChoice(
        tier="low", model="llama3.2:1b", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )

    with (
        patch(
            "regradar.agents.analysis_agent._get_llm_client",
            return_value=(primary_client, "llama3.1", primary_choice),
        ),
        patch(
            "regradar.agents.analysis_agent.other_tier_choice",
            return_value=fallback_choice,
        ),
        patch(
            "regradar.agents.analysis_agent.build_client",
            return_value=fallback_client,
        ),
    ):
        result = analyze_node(_make_state_with_chunks())

    assert result.extraction is None
```

- [ ] **Step 3: Run tests to verify they fail as expected**

Run: `pytest tests/unit/agents/test_analysis_agent.py -v`
Expected: the updated existing tests fail (mismatch — current `_get_llm_client()` takes no args, mocks now expect it to accept `risk_level` and current code doesn't consume `state.risk_level` at all); the 3 new tests fail with `ImportError`/`AttributeError` (`other_tier_choice`/`build_client` don't exist as importable names in `analysis_agent.py` yet).

- [ ] **Step 4: Update `analysis_agent.py`**

Add these imports at the top of `src/regradar/agents/analysis_agent.py`, alongside the existing `from openai import OpenAI` line:

```python
from openai import APIConnectionError, OpenAI, RateLimitError
```

Add this import alongside `from regradar.agents.state import ExtractionResult, PipelineState`:

```python
from regradar.llm_routing.tiered_router import ModelChoice, build_client, other_tier_choice, select_model
from regradar.models.enums import RiskLevel
```

Replace the existing `_get_llm_client` function:

```python
def _get_llm_client() -> tuple[OpenAI, str]:
    settings = get_settings()
    if settings.use_local_llm:
        return (
            OpenAI(base_url=settings.local_llm_base_url, api_key="ollama-local"),
            settings.local_llm_model,
        )
    return OpenAI(api_key=settings.openai_api_key.get_secret_value()), settings.tier_high_model
```

with:

```python
def _get_llm_client(risk_level: RiskLevel | None) -> tuple[OpenAI, str, ModelChoice]:
    choice = select_model(risk_level, task="analysis")
    return build_client(choice), choice.model, choice
```

`get_settings` is no longer used directly in this file if it has no other callers — check the rest of the file; if `get_settings` is unused after this change, remove its import (`from regradar.core.config import get_settings`). If it's still referenced elsewhere in the file, leave the import.

In `analyze_node`, replace this section:

```python
    prompt = _build_extraction_prompt(state)
    client, model_name = _get_llm_client()

    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            parsed = _call_extraction_model(client, model_name, prompt, strict_retry=attempt > 0)
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

with:

```python
    prompt = _build_extraction_prompt(state)
    client, model_name, choice = _get_llm_client(state.risk_level)

    last_error: Exception | None = None
    used_fallback = False
    for attempt in range(MAX_ATTEMPTS):
        try:
            parsed = _call_extraction_model(client, model_name, prompt, strict_retry=attempt > 0)
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
        except (APIConnectionError, RateLimitError) as exc:
            last_error = exc
            if used_fallback:
                logger.error(
                    "Fallback tier also unavailable for filing %s: %s", state.filing_id, exc
                )
                break
            logger.warning(
                "Primary tier %r unavailable for filing %s (%s); falling back to the other tier",
                choice.tier,
                state.filing_id,
                exc,
            )
            choice = other_tier_choice(choice, task="analysis")
            client = build_client(choice)
            model_name = choice.model
            used_fallback = True
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

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/agents/test_analysis_agent.py -v`
Expected: all tests (existing, updated for the 3-tuple, and the 3 new ones) PASS.

- [ ] **Step 6: Run the full unit test suite to check for regressions**

Run: `pytest tests/unit -v`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/regradar/agents/analysis_agent.py tests/unit/agents/test_analysis_agent.py
git commit -m "Route Analysis Agent through tiered model selection with fallback (AGENT-09)"
```

---

## Task 3: Wire tiered routing into the Summarization Agent

**Files:**
- Modify: `src/regradar/agents/summarization_agent.py`
- Modify: `tests/unit/agents/test_summarization_agent.py`

**Interfaces:**
- Consumes: same router functions as Task 2 (`select_model`, `other_tier_choice`, `build_client`, `ModelChoice` from `regradar.llm_routing.tiered_router`), same `openai.APIConnectionError`/`RateLimitError`.
- Produces: `summarize_node` routes through the shared router based on `state.risk_level`, with the same one-shot fallback. `_get_llm_client` in this file changes from `() -> tuple[OpenAI, str]` to `(risk_level: RiskLevel | None) -> tuple[OpenAI, str, ModelChoice]`, mirroring Task 2 exactly.

This task is structurally identical to Task 2, applied to `summarization_agent.py`/`test_summarization_agent.py` instead of the analysis-agent files. Follow the same steps:

- [ ] **Step 1: Update existing tests for the new `_get_llm_client` signature**

In `tests/unit/agents/test_summarization_agent.py`, add the same imports:

```python
from regradar.llm_routing.tiered_router import ModelChoice
```

Add the same helper:

```python
def _fake_model_choice(model: str = "llama3.1") -> ModelChoice:
    return ModelChoice(tier="high", model=model, base_url="http://localhost:11434/v1", api_key="ollama-local")
```

Update every `patch("regradar.agents.summarization_agent._get_llm_client", return_value=(...))` call site from a 2-tuple to a 3-tuple (add `, _fake_model_choice()` as the third element), across all 6 existing tests. Leave `test_summarize_node_with_no_extraction_leaves_briefs_none` as-is (it asserts `mock_get_client.assert_not_called()`, no `return_value` to update).

- [ ] **Step 2: Write the new failing tests for risk-based routing and fallback**

Add to `tests/unit/agents/test_summarization_agent.py`:

```python
from openai import APIConnectionError

from regradar.llm_routing.tiered_router import ModelChoice
from regradar.models.enums import RiskLevel


def test_summarize_node_passes_state_risk_level_to_get_llm_client() -> None:
    content = json.dumps(VALID_SUMMARIZATION_JSON)
    state = _make_state_with_extraction()

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(_mock_openai_client(content), "llama3.1", _fake_model_choice()),
    ) as mock_get_client:
        summarize_node(state)

    mock_get_client.assert_called_once_with(state.risk_level)


def test_summarize_node_falls_back_to_other_tier_on_connection_error() -> None:
    content = json.dumps(VALID_SUMMARIZATION_JSON)
    primary_client = MagicMock()
    primary_client.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())
    fallback_client = _mock_openai_client(content)

    primary_choice = ModelChoice(
        tier="high", model="llama3.1", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )
    fallback_choice = ModelChoice(
        tier="low", model="llama3.2:1b", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )

    with (
        patch(
            "regradar.agents.summarization_agent._get_llm_client",
            return_value=(primary_client, "llama3.1", primary_choice),
        ),
        patch(
            "regradar.agents.summarization_agent.other_tier_choice",
            return_value=fallback_choice,
        ),
        patch(
            "regradar.agents.summarization_agent.build_client",
            return_value=fallback_client,
        ),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is not None
    assert result.briefs.model_used == "llama3.2:1b"


def test_summarize_node_gives_up_after_fallback_also_fails() -> None:
    primary_client = MagicMock()
    primary_client.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())
    fallback_client = MagicMock()
    fallback_client.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())

    primary_choice = ModelChoice(
        tier="high", model="llama3.1", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )
    fallback_choice = ModelChoice(
        tier="low", model="llama3.2:1b", base_url="http://localhost:11434/v1", api_key="ollama-local"
    )

    with (
        patch(
            "regradar.agents.summarization_agent._get_llm_client",
            return_value=(primary_client, "llama3.1", primary_choice),
        ),
        patch(
            "regradar.agents.summarization_agent.other_tier_choice",
            return_value=fallback_choice,
        ),
        patch(
            "regradar.agents.summarization_agent.build_client",
            return_value=fallback_client,
        ),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is None
```

- [ ] **Step 3: Run tests to verify they fail as expected**

Run: `pytest tests/unit/agents/test_summarization_agent.py -v`
Expected: updated existing tests fail (signature mismatch), 3 new tests fail with `ImportError`/`AttributeError`.

- [ ] **Step 4: Update `summarization_agent.py`**

Add imports at the top, alongside `from openai import OpenAI`:

```python
from openai import APIConnectionError, OpenAI, RateLimitError
```

Alongside `from regradar.agents.state import BriefSet, PipelineState`:

```python
from regradar.llm_routing.tiered_router import ModelChoice, build_client, other_tier_choice, select_model
from regradar.models.enums import RiskLevel
```

Replace the existing `_get_llm_client`:

```python
def _get_llm_client() -> tuple[OpenAI, str]:
    settings = get_settings()
    if settings.use_local_llm:
        return (
            OpenAI(base_url=settings.local_llm_base_url, api_key="ollama-local"),
            settings.local_llm_model,
        )
    return OpenAI(api_key=settings.openai_api_key.get_secret_value()), settings.tier_high_model
```

with:

```python
def _get_llm_client(risk_level: RiskLevel | None) -> tuple[OpenAI, str, ModelChoice]:
    choice = select_model(risk_level, task="summarization")
    return build_client(choice), choice.model, choice
```

Remove the `from regradar.core.config import get_settings` import if `get_settings` has no other callers left in this file (check first).

In `summarize_node`, replace:

```python
    prompt = _build_summarization_prompt(state)
    client, model_name = _get_llm_client()

    last_error: Exception | None = None
    retry_issue: str | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            parsed = _call_summarization_model(client, model_name, prompt, retry_issue)
            _validate_summarization(parsed)
            briefs = BriefSet(
                executive_brief=parsed["executive_brief"],
                cco_summary=parsed["cco_summary"],
                analyst_summary=parsed["analyst_summary"],
                engineer_summary=_build_engineer_summary(state),
                model_used=model_name,
            )
            return state.model_copy(update={"briefs": briefs})
        except (json.JSONDecodeError, SummarizationError) as exc:
            last_error = exc
            retry_issue = str(exc)
            logger.warning(
                "Summarization attempt %d failed for filing %s: %s",
                attempt + 1,
                state.filing_id,
                exc,
            )

    logger.error(
        "Summarization failed for filing %s after %d attempts: %s",
        state.filing_id,
        MAX_ATTEMPTS,
        last_error,
    )
    return state
```

with:

```python
    prompt = _build_summarization_prompt(state)
    client, model_name, choice = _get_llm_client(state.risk_level)

    last_error: Exception | None = None
    retry_issue: str | None = None
    used_fallback = False
    for attempt in range(MAX_ATTEMPTS):
        try:
            parsed = _call_summarization_model(client, model_name, prompt, retry_issue)
            _validate_summarization(parsed)
            briefs = BriefSet(
                executive_brief=parsed["executive_brief"],
                cco_summary=parsed["cco_summary"],
                analyst_summary=parsed["analyst_summary"],
                engineer_summary=_build_engineer_summary(state),
                model_used=model_name,
            )
            return state.model_copy(update={"briefs": briefs})
        except (APIConnectionError, RateLimitError) as exc:
            last_error = exc
            if used_fallback:
                logger.error(
                    "Fallback tier also unavailable for filing %s: %s", state.filing_id, exc
                )
                break
            logger.warning(
                "Primary tier %r unavailable for filing %s (%s); falling back to the other tier",
                choice.tier,
                state.filing_id,
                exc,
            )
            choice = other_tier_choice(choice, task="summarization")
            client = build_client(choice)
            model_name = choice.model
            used_fallback = True
        except (json.JSONDecodeError, SummarizationError) as exc:
            last_error = exc
            retry_issue = str(exc)
            logger.warning(
                "Summarization attempt %d failed for filing %s: %s",
                attempt + 1,
                state.filing_id,
                exc,
            )

    logger.error(
        "Summarization failed for filing %s after %d attempts: %s",
        state.filing_id,
        MAX_ATTEMPTS,
        last_error,
    )
    return state
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/agents/test_summarization_agent.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Run the full unit test suite to check for regressions**

Run: `pytest tests/unit -v`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/regradar/agents/summarization_agent.py tests/unit/agents/test_summarization_agent.py
git commit -m "Route Summarization Agent through tiered model selection with fallback (AGENT-09)"
```

---

## Task 4: Live verification — pull the local low-tier model and confirm real tiered routing end-to-end

Per this project's established live-verification policy, run real tiered routing against real local Ollama before considering AGENT-09 done — not just mocked unit tests. **Note on fallback verification**: the connection/rate-limit fallback path is verified via the mocked unit tests in Tasks 2-3 only — in local mode both tiers hit the same local Ollama server, so there's no safe, realistic way to simulate a live provider-specific outage for one tier without also breaking the other; this is called out explicitly rather than skipped silently.

**Files:** none (manual verification, no code changes; `.env` gets a new local value, not committed — it's gitignored).

- [ ] **Step 1: Pull the low-tier model**

Run: `ollama pull llama3.2:1b`
Confirm: `ollama list` shows both `llama3.1` and `llama3.2:1b`.

- [ ] **Step 2: Set the new config value locally**

Add `LOCAL_LLM_LOW_MODEL=llama3.2:1b` to the local `.env` file (the user's real `.env`, never pasted into chat — confirm it's set with `grep LOCAL_LLM_LOW_MODEL .env`, don't print the full file). Confirm `USE_LOCAL_LLM=true` is already set (it should be, from prior tickets).

- [ ] **Step 3: Start Ollama and run a real tiered-routing call for each tier**

Start Ollama (`ollama serve`, backgrounded, per the project's no-unsupervised-background-services policy — start only for this verification, stop immediately after).

Write a short throwaway script (not committed) that:
1. Calls `select_model(RiskLevel.LOW, task="analysis")` and confirms `choice.model == "llama3.2:1b"`.
2. Calls `select_model(RiskLevel.CRITICAL, task="analysis")` and confirms `choice.model == "llama3.1"`.
3. Constructs a fixture `PipelineState` with `risk_level=RiskLevel.LOW` and a populated `chunks` list, calls `analyze_node(state)` directly (real Ollama call, no mocking), and confirms `result.extraction.model_used == "llama3.2:1b"` and that a real (if smaller/lower-quality) extraction came back.
4. Repeats step 3 with `risk_level=RiskLevel.CRITICAL` and confirms `result.extraction.model_used == "llama3.1"`.
5. Repeats steps 3-4 for `summarize_node` with a populated `extraction` field on the state, confirming `result.briefs.model_used` matches the expected tier's model for both a low-risk and a critical-risk state.

Print all results for manual inspection — confirm both tiers produce valid, schema-conformant output (not just that the right model name was recorded), and that the low-tier model's output, while likely shorter/less detailed given its much smaller size, is still valid and doesn't crash the validation logic.

- [ ] **Step 4: Stop Ollama**

Per the project's no-unsupervised-background-services policy: `pkill ollama` (or however it was started). Remove the throwaway verification script.

- [ ] **Step 5: Update project memory**

Record the outcome of live verification (both tiers confirmed working with real, distinct model output; fallback path verified only via mocks, with the reasoning why) — this happens outside the plan file, as a memory update once the ticket is complete.

---

## Self-Review Notes

- **Spec coverage:** All 4 AGENT-09 acceptance criteria covered — (1) Critical/High → high tier, Low/Medium → low tier, verified for all four risk levels in Task 1's tests (`test_select_model_routes_low_and_medium_to_low_tier`, `test_select_model_routes_high_and_critical_to_high_tier`); (2) threshold is `settings.tier_routing_risk_threshold`, a config value, not hardcoded (`test_select_model_respects_custom_threshold`); (3) fallback to the other tier on a connection/rate-limit error, verified in Tasks 2 & 3's `*_falls_back_to_other_tier_on_connection_error` tests for both agents; (4) tests confirm routing for all four risk levels plus a simulated outage triggering fallback — the ticket's literal "simulated OpenAI outage triggers fallback to Granite" is satisfied in spirit (simulated connection-error triggers fallback to the other tier), with the local-mode substitution documented as a deliberate, user-confirmed deviation.
- **Placeholder scan:** No TBD/TODO markers; every code block is complete and copy-pasteable, all test bodies contain real assertions.
- **Type consistency:** `ModelChoice` fields (`tier`, `model`, `base_url`, `api_key`) used identically across Task 1 (definition), Task 2, and Task 3 (both agents' `_get_llm_client` return it as the third tuple element and pass it to `other_tier_choice`/`build_client`). `select_model`/`select_model_for_tier`/`other_tier_choice` signatures match between their Task 1 definition and Tasks 2-3's call sites exactly (`task: Literal["analysis", "summarization"]` matches the literal string `"analysis"`/`"summarization"` passed at each call site).
