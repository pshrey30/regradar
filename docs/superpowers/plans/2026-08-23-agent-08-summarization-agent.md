# AGENT-08 — Summarization Agent (Persona Briefs) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `summarize_node` stub in `agents/graph.py` with a real implementation that generates four persona-tailored brief fields (`executive_brief`, `cco_summary`, `analyst_summary`, `engineer_summary`) from AGENT-07's extraction result, and persist them to the `briefs` table.

**Architecture:** A new `agents/summarization_agent.py` mirrors AGENT-07's `analysis_agent.py` pattern exactly: a sync, no-DB `summarize_node(state) -> state` function that calls a local Ollama (or real OpenAI, same `USE_LOCAL_LLM` toggle) chat-completions endpoint with a JSON-schema-enforced response, validates the response (including a sentence-count check on `executive_brief` and a word-count check on `cco_summary`), retries once with a stricter prompt on failure, and gives up (leaving `state.briefs = None`) after `MAX_ATTEMPTS`. `engineer_summary` is **not** LLM-generated — it's built deterministically from fields already in `PipelineState` (filing_id, domain, risk_level, obligation count), since the spec describes it as "a one-line confirmation... not a narrative summary" and PipelineState has no `filing_type`/URL field an LLM could ground a real link in. This mirrors AGENT-07's precedent of documented, deliberate spec deviations where the literal ticket wording doesn't fit what's actually available in the pipeline. `workers/pipeline_tasks.py` persists the result the same way it persists `Extraction`, and a filing whose extraction succeeded but summarization didn't is marked `NEEDS_REVIEW`, extending the existing pattern used for extraction failures.

**Tech Stack:** Python 3.11, Pydantic v2, `openai` SDK (pointed at local Ollama via `OPENAI_BASE_URL` override, `settings.use_local_llm`), LangGraph, pytest + `unittest.mock`.

## Global Constraints

- No real network/LLM calls in unit tests — the OpenAI-compatible client is always mocked, exactly like `tests/unit/agents/test_analysis_agent.py`.
- `summarize_node` stays a plain **sync** function — no DB access inside the node (DB writes happen in `workers/pipeline_tasks.py` after the graph runs), matching `analyze_node`'s pattern and the project's rule that async is reserved for nodes that structurally need mid-graph I/O.
- Reuse `settings.use_local_llm` / `settings.local_llm_base_url` / `settings.local_llm_model` / `settings.tier_high_model` / `settings.openai_api_key` — no new config fields needed.
- No new Alembic migration — the `briefs` table and `Brief` ORM model already exist (migration `0002_create_core_tables.py`, `src/regradar/models/brief.py`) and match `BriefSet`'s fields 1:1.
- `state.model_copy(update={"briefs": briefs})` on success; return `state` unchanged (leaving `briefs=None`) on unrecoverable failure — never raise out of `summarize_node`.
- `result["briefs"]` from `ainvoke()` is a real `BriefSet` Pydantic instance, not a dict — use attribute access, never `["..."]` subscript, per the verified AGENT-07 gotcha documented in `pipeline_tasks.py`.

---

## Task 1: Implement `summarization_agent.py` with unit tests

**Files:**
- Create: `src/regradar/agents/summarization_agent.py`
- Create: `tests/unit/agents/test_summarization_agent.py`

**Interfaces:**
- Consumes: `PipelineState` (fields: `filing_id: uuid.UUID`, `domain: FilingDomain | None`, `risk_level: RiskLevel | None`, `extraction: ExtractionResult | None`) and `BriefSet` from `regradar.agents.state`; `ExtractionResult` fields: `obligations: list[dict]`, `deadlines: list[dict]`, `risk_flags: list[str]`, `affected_products: list[str]`, `key_entities: list[str]`, `competitor_mentions: list[str]`; `get_settings()` from `regradar.core.config` (fields: `use_local_llm: bool`, `local_llm_base_url: str`, `local_llm_model: str`, `openai_api_key: SecretStr`, `tier_high_model: str`).
- Produces: `summarize_node(state: PipelineState) -> PipelineState` — the public entry point Task 2 wires into the graph. On success, `result.briefs` is a `BriefSet` with all four fields populated and `model_used` set to the model name used. On failure, `result.briefs is None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/agents/test_summarization_agent.py`:

```python
"""Unit tests for the Summarization Agent's persona-brief generation call.

The OpenAI-compatible client is always mocked — no real Ollama or OpenAI
call in these tests, mirroring test_analysis_agent.py.
"""

import json
import uuid
from unittest.mock import MagicMock, patch

from regradar.agents.state import ExtractionResult, PipelineState
from regradar.agents.summarization_agent import summarize_node
from regradar.models.enums import FilingDomain, RiskLevel

VALID_SUMMARIZATION_JSON = {
    "executive_brief": (
        "Acme Corp filed an annual compliance certification. The filing flags a "
        "material weakness in internal controls. A remediation deadline of "
        "January 15, 2027 applies."
    ),
    "cco_summary": "Acme Corp: material weakness flagged, high risk, remediation due Jan 2027.",
    "analyst_summary": (
        "- File annual compliance certification (due 2027-01-15)\n"
        "- Remediate flagged material weakness"
    ),
}


def _mock_openai_client(content: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    client.chat.completions.create.return_value = response
    return client


def _make_state_with_extraction(obligation_count: int = 1) -> PipelineState:
    extraction = ExtractionResult(
        obligations=[
            {"description": f"Obligation {i}.", "source_chunk_index": 0}
            for i in range(obligation_count)
        ],
        deadlines=[{"description": "Annual compliance certification", "date": "2027-01-15"}],
        risk_flags=["material weakness"],
        affected_products=[],
        key_entities=["Acme Corp"],
        competitor_mentions=[],
        model_used="llama3.1",
    )
    return PipelineState(
        filing_id=uuid.uuid4(),
        raw_text="Full filing text.",
        domain=FilingDomain.FINANCIAL,
        risk_level=RiskLevel.HIGH,
        extraction=extraction,
    )


def test_summarize_node_populates_briefs_on_valid_response() -> None:
    content = json.dumps(VALID_SUMMARIZATION_JSON)
    state = _make_state_with_extraction(obligation_count=1)

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(_mock_openai_client(content), "llama3.1"),
    ):
        result = summarize_node(state)

    assert result.briefs is not None
    assert result.briefs.executive_brief == VALID_SUMMARIZATION_JSON["executive_brief"]
    assert result.briefs.cco_summary == VALID_SUMMARIZATION_JSON["cco_summary"]
    assert result.briefs.analyst_summary == VALID_SUMMARIZATION_JSON["analyst_summary"]
    assert result.briefs.model_used == "llama3.1"
    # engineer_summary is built deterministically, not from the LLM response.
    assert str(state.filing_id) in result.briefs.engineer_summary
    assert "domain=financial" in result.briefs.engineer_summary
    assert "risk_level=high" in result.briefs.engineer_summary
    assert "obligations_extracted=1" in result.briefs.engineer_summary


def test_summarize_node_retries_once_on_malformed_json_then_succeeds() -> None:
    valid_content = json.dumps(VALID_SUMMARIZATION_JSON)
    client = MagicMock()
    malformed_response = MagicMock()
    malformed_response.choices = [MagicMock(message=MagicMock(content="not valid json"))]
    valid_response = MagicMock()
    valid_response.choices = [MagicMock(message=MagicMock(content=valid_content))]
    client.chat.completions.create.side_effect = [malformed_response, valid_response]

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(client, "llama3.1"),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is not None
    assert client.chat.completions.create.call_count == 2


def test_summarize_node_leaves_briefs_none_after_two_malformed_responses() -> None:
    client = MagicMock()
    malformed_response = MagicMock()
    malformed_response.choices = [MagicMock(message=MagicMock(content="not valid json"))]
    client.chat.completions.create.return_value = malformed_response

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(client, "llama3.1"),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is None
    assert client.chat.completions.create.call_count == 2


def test_summarize_node_retries_when_executive_brief_sentence_count_out_of_range() -> None:
    bad_json = dict(VALID_SUMMARIZATION_JSON)
    bad_json["executive_brief"] = "Too short."
    good_content = json.dumps(VALID_SUMMARIZATION_JSON)
    client = MagicMock()
    bad_response = MagicMock()
    bad_response.choices = [MagicMock(message=MagicMock(content=json.dumps(bad_json)))]
    good_response = MagicMock()
    good_response.choices = [MagicMock(message=MagicMock(content=good_content))]
    client.chat.completions.create.side_effect = [bad_response, good_response]

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(client, "llama3.1"),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is not None
    assert client.chat.completions.create.call_count == 2


def test_summarize_node_retries_when_cco_summary_exceeds_fifty_words() -> None:
    bad_json = dict(VALID_SUMMARIZATION_JSON)
    bad_json["cco_summary"] = " ".join(["word"] * 60)
    good_content = json.dumps(VALID_SUMMARIZATION_JSON)
    client = MagicMock()
    bad_response = MagicMock()
    bad_response.choices = [MagicMock(message=MagicMock(content=json.dumps(bad_json)))]
    good_response = MagicMock()
    good_response.choices = [MagicMock(message=MagicMock(content=good_content))]
    client.chat.completions.create.side_effect = [bad_response, good_response]

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(client, "llama3.1"),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is not None
    assert client.chat.completions.create.call_count == 2


def test_summarize_node_leaves_briefs_none_on_wrong_typed_field_without_crashing() -> None:
    bad_json = dict(VALID_SUMMARIZATION_JSON)
    bad_json["analyst_summary"] = None
    client = MagicMock()
    bad_response = MagicMock()
    bad_response.choices = [MagicMock(message=MagicMock(content=json.dumps(bad_json)))]
    client.chat.completions.create.return_value = bad_response

    with patch(
        "regradar.agents.summarization_agent._get_llm_client",
        return_value=(client, "llama3.1"),
    ):
        result = summarize_node(_make_state_with_extraction())

    assert result.briefs is None
    assert client.chat.completions.create.call_count == 2


def test_summarize_node_with_no_extraction_leaves_briefs_none() -> None:
    state = PipelineState(filing_id=uuid.uuid4(), raw_text="", extraction=None)

    with patch("regradar.agents.summarization_agent._get_llm_client") as mock_get_client:
        result = summarize_node(state)

    assert result.briefs is None
    mock_get_client.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/agents/test_summarization_agent.py -v`
Expected: `ModuleNotFoundError: No module named 'regradar.agents.summarization_agent'` (or `ImportError`) for every test.

- [ ] **Step 3: Write the implementation**

Create `src/regradar/agents/summarization_agent.py`:

```python
"""The real Summarization Agent — persona brief generation via local Ollama
(or real OpenAI, same USE_LOCAL_LLM toggle as triage_agent.py and
analysis_agent.py).

summarize_node is a plain sync function — no DB access. It reads
state.extraction (AGENT-07's output); the briefs-table INSERT happens in
workers/pipeline_tasks.py after the graph, mirroring how extraction is
persisted.

Deviates from the ticket's literal "engineer_summary" wording ("filing
type, risk level, and a link reference") — PipelineState carries no
filing_type or a browsable URL to link to, and the ticket itself
describes this persona's summary as "nothing filing-specific beyond
confirming pipeline completion... the shortest". Building it
deterministically from fields already in PipelineState (filing_id,
domain, risk_level, obligation count) instead of an LLM call is both
cheaper and immune to hallucination for a field with no narrative
content to summarize.
"""

import json
import logging
import re

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONSchema

from regradar.agents.state import BriefSet, PipelineState
from regradar.core.config import get_settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
MAX_EXECUTIVE_BRIEF_SENTENCES = 5
MIN_EXECUTIVE_BRIEF_SENTENCES = 3
MAX_CCO_SUMMARY_WORDS = 50

SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a regulatory compliance summarization assistant. Given a filing's "
    "extracted obligations, deadlines, risk flags, affected products, key entities, "
    "domain, and risk level, produce a JSON object with exactly three fields: "
    "executive_brief (a plain-English summary of the filing in EXACTLY 3 to 5 complete "
    "sentences), cco_summary (board-level framing — what happened, why it matters, and "
    "the risk level — in under 50 words), and analyst_summary (a short bulleted list, "
    "using '- ' at the start of each line, covering the specific obligations and "
    "deadlines extracted). Respond with strict JSON only, matching the required schema "
    "exactly."
)

SUMMARIZATION_RETRY_SUFFIX = (
    " The previous response was invalid: {issue} Respond again with strict, "
    "schema-conformant JSON only, and make sure executive_brief contains exactly 3 to 5 "
    "complete sentences and cco_summary stays under 50 words."
)

SUMMARIZATION_SCHEMA = {
    "type": "object",
    "properties": {
        "executive_brief": {"type": "string"},
        "cco_summary": {"type": "string"},
        "analyst_summary": {"type": "string"},
    },
    "required": ["executive_brief", "cco_summary", "analyst_summary"],
}


class SummarizationError(Exception):
    """Raised internally when brief generation fails validation after retry —
    caught by summarize_node, never propagates out of it."""


def _get_llm_client() -> tuple[OpenAI, str]:
    settings = get_settings()
    if settings.use_local_llm:
        return (
            OpenAI(base_url=settings.local_llm_base_url, api_key="ollama-local"),
            settings.local_llm_model,
        )
    return OpenAI(api_key=settings.openai_api_key.get_secret_value()), settings.tier_high_model


def _build_summarization_prompt(state: PipelineState) -> str:
    extraction = state.extraction
    assert extraction is not None  # guarded by summarize_node before this is called
    domain = state.domain.value if state.domain else "unknown"
    risk_level = state.risk_level.value if state.risk_level else "unknown"
    obligations = "\n".join(f"- {o.get('description', o)}" for o in extraction.obligations) or "None"
    deadlines = "\n".join(f"- {d.get('description', d)}: {d.get('date', '')}" for d in extraction.deadlines) or "None"
    return (
        f"Domain: {domain}\n"
        f"Risk level: {risk_level}\n"
        f"Obligations:\n{obligations}\n"
        f"Deadlines:\n{deadlines}\n"
        f"Risk flags: {', '.join(extraction.risk_flags) or 'None'}\n"
        f"Affected products: {', '.join(extraction.affected_products) or 'None'}\n"
        f"Key entities: {', '.join(extraction.key_entities) or 'None'}"
    )


def _call_summarization_model(
    client: OpenAI, model: str, prompt: str, retry_issue: str | None
) -> dict:
    system_prompt = SUMMARIZATION_SYSTEM_PROMPT
    if retry_issue is not None:
        system_prompt += SUMMARIZATION_RETRY_SUFFIX.format(issue=retry_issue)
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    response_format: ResponseFormatJSONSchema = {
        "type": "json_schema",
        "json_schema": {
            "name": "summarization",
            "schema": SUMMARIZATION_SCHEMA,
            "strict": True,
        },
    }
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format=response_format,
        temperature=0,
    )
    content = response.choices[0].message.content or ""
    return json.loads(content)


def _count_sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


def _validate_summarization(parsed: dict) -> None:
    """Validate presence, type, and length constraints of every required field.

    Any malformed shape raises SummarizationError so summarize_node's
    except clause treats it as a validation failure — never lets a raw
    TypeError/AttributeError escape and crash the pipeline.
    """
    try:
        for key in ("executive_brief", "cco_summary", "analyst_summary"):
            if key not in parsed:
                raise SummarizationError(f"Missing required field: {key}")
            if not isinstance(parsed[key], str):
                raise SummarizationError(
                    f"Field {key!r} must be a string, got {type(parsed[key]).__name__}"
                )

        sentence_count = _count_sentences(parsed["executive_brief"])
        if not (MIN_EXECUTIVE_BRIEF_SENTENCES <= sentence_count <= MAX_EXECUTIVE_BRIEF_SENTENCES):
            raise SummarizationError(
                f"executive_brief must be {MIN_EXECUTIVE_BRIEF_SENTENCES}-"
                f"{MAX_EXECUTIVE_BRIEF_SENTENCES} sentences, got {sentence_count}"
            )

        word_count = len(parsed["cco_summary"].split())
        if word_count > MAX_CCO_SUMMARY_WORDS:
            raise SummarizationError(
                f"cco_summary must be under {MAX_CCO_SUMMARY_WORDS} words, got {word_count}"
            )
    except SummarizationError:
        raise
    except Exception as exc:
        raise SummarizationError(f"Malformed summarization response: {exc}") from exc


def _build_engineer_summary(state: PipelineState) -> str:
    domain = state.domain.value if state.domain else "unknown"
    risk_level = state.risk_level.value if state.risk_level else "unknown"
    obligation_count = len(state.extraction.obligations) if state.extraction else 0
    return (
        f"filing_id={state.filing_id} domain={domain} risk_level={risk_level} "
        f"obligations_extracted={obligation_count} status=processed"
    )


def summarize_node(state: PipelineState) -> PipelineState:
    """The real summarize node — replaces AGENT-01's passthrough stub.

    On success, sets state.briefs. On failure after one retry with a
    stricter prompt, leaves state.briefs at its default None —
    workers/pipeline_tasks.py reads this the same way it reads a missing
    extraction: as the signal to mark the filing needs_review.
    """
    if state.extraction is None:
        logger.warning(
            "No extraction available for filing %s; skipping summarization", state.filing_id
        )
        return state

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/agents/test_summarization_agent.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Run the full unit test suite to check for regressions**

Run: `pytest tests/unit -v`
Expected: all tests PASS (no regressions in unrelated modules).

- [ ] **Step 6: Commit**

```bash
git add src/regradar/agents/summarization_agent.py tests/unit/agents/test_summarization_agent.py
git commit -m "Add persona-brief Summarization Agent (AGENT-08)"
```

---

## Task 2: Wire the real `summarize_node` into the graph

**Files:**
- Modify: `src/regradar/agents/graph.py`
- Modify: `tests/unit/agents/test_graph.py`

**Interfaces:**
- Consumes: `summarize_node` from `regradar.agents.summarization_agent` (produced by Task 1).
- Produces: `agents/graph.py`'s compiled graph now calls the real summarization logic between `analyze` and `deliver`.

- [ ] **Step 1: Update the failing/stale test first**

`tests/unit/agents/test_graph.py` currently parametrizes `test_stub_node_returns_state_unchanged` over `[deliver_node, summarize_node]` — once `summarize_node` is real, it must be dropped from that stub-only parametrize list (it's no longer a no-op stub, and it would fail that test since a `PipelineState` with no `extraction` still returns the state unchanged, but that's a *different* code path than what the stub test is asserting for `deliver_node`; keeping it in this list would be testing the wrong thing).

Edit `tests/unit/agents/test_graph.py`:

```python
"""Unit tests for the LangGraph stub nodes and triage routing decision.

Each node is called directly (not via a compiled graph) so it can be
tested in isolation, per AGENT-01's acceptance criteria.
"""

import uuid

import pytest

from regradar.agents.graph import (
    deliver_node,
    route_after_triage,
)
from regradar.agents.state import PipelineState
from regradar.models.enums import RiskLevel


def _make_state(risk_level: RiskLevel | None = None) -> PipelineState:
    return PipelineState(filing_id=uuid.uuid4(), raw_text="filing text", risk_level=risk_level)


@pytest.mark.parametrize(
    "node",
    [deliver_node],
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

- [ ] **Step 2: Run the test file to confirm it still passes with the stub in place**

Run: `pytest tests/unit/agents/test_graph.py -v`
Expected: all tests PASS (this step doesn't test new behavior yet — it just confirms removing `summarize_node` from the stub-parametrize list didn't break anything, before the node itself changes underneath it).

- [ ] **Step 3: Wire the real `summarize_node` into `graph.py`**

Edit `src/regradar/agents/graph.py`:

```python
"""The LangGraph supervisor graph wiring the six pipeline agents together.

triage_node, retrieve_node, analyze_node, and summarize_node are real
implementations (AGENT-02, AGENT-06, AGENT-07, AGENT-08) — deliver_node
is still a stub for AGENT-10. retrieve_node is the only async node; the
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
from regradar.agents.summarization_agent import summarize_node
from regradar.agents.triage_agent import triage_node
from regradar.models.enums import RiskLevel


def deliver_node(state: PipelineState) -> PipelineState:
    """Stub — replaced by the Slack/email/webhook fan-out agent in AGENT-10."""
    return state
```

Remove the old `summarize_node` stub definition entirely (both the function body and its docstring) — the import above replaces it. Leave `route_after_triage` and `build_graph` unchanged (the `graph.add_node("summarize", summarize_node)` line already references the name `summarize_node`, which now resolves to the imported real function instead of the local stub).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/agents/test_graph.py tests/unit/agents/test_summarization_agent.py tests/integration/test_pipeline_graph.py -v`
Expected: all PASS. If `test_pipeline_graph.py` builds a fixture `PipelineState` and runs it through the full compiled graph, confirm it still reaches `deliver` without error (a filing with no `extraction` set will simply pass through `summarize_node` unchanged, same as it does today with a stub).

- [ ] **Step 5: Commit**

```bash
git add src/regradar/agents/graph.py tests/unit/agents/test_graph.py
git commit -m "Wire real summarize_node into the graph (AGENT-08)"
```

---

## Task 3: Persist `Brief` in `pipeline_tasks.py`

**Files:**
- Modify: `src/regradar/workers/pipeline_tasks.py`
- Modify: `tests/unit/workers/test_pipeline_tasks.py`

**Interfaces:**
- Consumes: `Brief` ORM model from `regradar.models.brief` (fields: `filing_id`, `executive_brief`, `cco_summary`, `analyst_summary`, `engineer_summary`, `model_used`); `result["briefs"]` — a `BriefSet` Pydantic instance (or `None`) returned by `ainvoke()`.
- Produces: after a successful pipeline run with both `extraction` and `briefs` populated, a `Brief` row exists in the DB matching the filing. A filing whose `extraction` succeeded but `briefs` came back `None` is marked `FilingStatus.NEEDS_REVIEW` (extending the existing extraction-failure branch, not a new status).

- [ ] **Step 1: Write the failing tests**

Add these three tests to `tests/unit/workers/test_pipeline_tasks.py` (append after `test_process_filing_marks_needs_review_when_extraction_fails`, keeping the existing imports at the top — add `from regradar.agents.state import BriefSet` alongside the existing `from regradar.agents.state import ExtractionResult`, and `from regradar.models.brief import Brief` alongside `from regradar.models.extraction import Extraction`):

```python
def test_process_filing_persists_briefs_on_success(
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
    extraction_result = ExtractionResult(
        obligations=[{"description": "File report.", "source_chunk_index": 0}],
        deadlines=[{"description": "Annual report", "date": "2027-01-01"}],
        risk_flags=["material weakness"],
        affected_products=[],
        key_entities=["Acme Corp"],
        competitor_mentions=[],
        model_used="llama3.1",
    )
    briefs_result = BriefSet(
        executive_brief="Sentence one. Sentence two. Sentence three.",
        cco_summary="Short board-level summary.",
        analyst_summary="- File report by 2027-01-01",
        engineer_summary=f"filing_id={filing_id} domain=financial risk_level=low obligations_extracted=1 status=processed",
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
                    "briefs": briefs_result,
                }
            )
        ),
    )

    process_filing.run(str(filing_id))

    assert mock_db.add.call_count == 2
    added_objects = [call.args[0] for call in mock_db.add.call_args_list]
    assert isinstance(added_objects[0], Extraction)
    added_brief = added_objects[1]
    assert isinstance(added_brief, Brief)
    assert added_brief.filing_id == filing_id
    assert added_brief.executive_brief == briefs_result.executive_brief
    assert added_brief.cco_summary == briefs_result.cco_summary
    assert added_brief.analyst_summary == briefs_result.analyst_summary
    assert added_brief.engineer_summary == briefs_result.engineer_summary
    assert added_brief.model_used == "llama3.1"
    assert filing.status == FilingStatus.CLASSIFYING


def test_process_filing_marks_needs_review_when_summarization_fails(
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
                    "briefs": None,
                }
            )
        ),
    )

    process_filing.run(str(filing_id))

    # Extraction still gets persisted even though summarization failed —
    # that successful result must not be discarded.
    assert mock_db.add.call_count == 1
    added_extraction = mock_db.add.call_args[0][0]
    assert isinstance(added_extraction, Extraction)
    assert filing.status == FilingStatus.NEEDS_REVIEW
```

Also update the two pre-existing tests that assert `filing.status == FilingStatus.CLASSIFYING` after a successful run with `"extraction": None` in the mocked `ainvoke()` return dict — `test_process_filing_persists_classification_on_success`, `test_process_filing_extracts_text_and_embeds_chunks_when_pdf_present`, and `test_process_filing_skips_extraction_when_no_pdf_key` all currently return a dict with `"extraction": None` and no `"briefs"` key at all. Add `"briefs": None` to each of those three mocked return dicts (find each `"extraction": None,` line inside a `return_value={...}` dict in those three tests and add `"briefs": None,` on the next line) — the real code will do `result["briefs"]`, so the mock dicts must include that key or the test will raise `KeyError` once Step 3 below lands.

- [ ] **Step 2: Run tests to verify they fail correctly**

Run: `pytest tests/unit/workers/test_pipeline_tasks.py -v`
Expected: `test_process_filing_persists_briefs_on_success` and `test_process_filing_marks_needs_review_when_summarization_fails` FAIL with `KeyError: 'briefs'` (the current `pipeline_tasks.py` doesn't read that key yet). The three tests you edited to add `"briefs": None` should still PASS at this point (the current code ignores the key, so adding it to the mock dict is harmless until Step 3 makes the code read it).

- [ ] **Step 3: Update `pipeline_tasks.py`**

In `src/regradar/workers/pipeline_tasks.py`, add the import:

```python
from regradar.models.brief import Brief
```

alongside the existing `from regradar.models.extraction import Extraction` line.

Replace the status-decision block (currently):

```python
        if result["domain"] is None:
            filing.status = FilingStatus.NEEDS_CLASSIFICATION
        else:
            filing.domain = result["domain"]
            filing.risk_level = result["risk_level"]
            filing.classification_confidence = result["classification_confidence"]
            if result["extraction"] is None and chunks:
                filing.status = FilingStatus.NEEDS_REVIEW
            else:
                filing.status = FilingStatus.CLASSIFYING
        await db.commit()
```

with:

```python
        if result["domain"] is None:
            filing.status = FilingStatus.NEEDS_CLASSIFICATION
        else:
            filing.domain = result["domain"]
            filing.risk_level = result["risk_level"]
            filing.classification_confidence = result["classification_confidence"]
            extraction_missing = result["extraction"] is None and chunks
            briefs_missing = result["extraction"] is not None and result["briefs"] is None
            if extraction_missing or briefs_missing:
                filing.status = FilingStatus.NEEDS_REVIEW
            else:
                filing.status = FilingStatus.CLASSIFYING
        await db.commit()
```

Then add a `Brief` persistence block immediately after the existing `Extraction` persistence block (after the `await db.commit()` that follows the `Extraction` insert, before the `if raw_text:` / `embed_chunks` block):

```python
        if result["briefs"] is not None:
            # result["briefs"] is a real BriefSet instance — same
            # ainvoke() nested-Pydantic-model behavior verified for
            # ExtractionResult in AGENT-07 — attribute access only.
            briefs_result = result["briefs"]
            db.add(
                Brief(
                    filing_id=filing.id,
                    executive_brief=briefs_result.executive_brief,
                    cco_summary=briefs_result.cco_summary,
                    analyst_summary=briefs_result.analyst_summary,
                    engineer_summary=briefs_result.engineer_summary,
                    model_used=briefs_result.model_used,
                )
            )
            await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/workers/test_pipeline_tasks.py -v`
Expected: all tests PASS, including the two new ones and the three edited ones.

- [ ] **Step 5: Run the full unit test suite to check for regressions**

Run: `pytest tests/unit -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/regradar/workers/pipeline_tasks.py tests/unit/workers/test_pipeline_tasks.py
git commit -m "Persist Summarization Agent briefs; mark needs_review on summarization failure (AGENT-08)"
```

---

## Task 4: Live verification against real Postgres + real local Ollama

Per this project's established live-verification policy, run the full path against real infrastructure before considering AGENT-08 done — not just mocked unit tests.

**Files:** none (manual verification task, no code changes).

- [ ] **Step 1: Start Ollama locally (if not already running)**

Run: `ollama serve &` (or confirm it's already running: `curl -s http://localhost:11434/api/tags`). Confirm `llama3.1` is available: `ollama list | grep llama3.1`.

- [ ] **Step 2: Confirm `USE_LOCAL_LLM=true` is set in `.env`**

Check: `grep USE_LOCAL_LLM .env` — per project convention this should already be set locally from AGENT-02/03/05/07's setup. If missing, ask the user to add it (never write real `.env` values yourself into the transcript).

- [ ] **Step 3: Run a real end-to-end pipeline call against a fixture filing with real extraction data, hitting real local Ollama**

Write a short throwaway script (not committed) that constructs a `PipelineState` with `filing_id`, `domain=FilingDomain.FINANCIAL`, `risk_level=RiskLevel.HIGH`, and a realistic `ExtractionResult` (2-3 obligations, a deadline, a risk flag), imports `summarize_node` from `regradar.agents.summarization_agent`, calls it directly (no mocking), and prints `result.briefs`. Confirm:
  - `executive_brief` is non-empty and contains 3-5 sentences by eye.
  - `cco_summary` reads as board-level framing under ~50 words.
  - `analyst_summary` is a bulleted list referencing the obligations/deadlines passed in.
  - `engineer_summary` exactly matches the deterministic format (`filing_id=... domain=... risk_level=... obligations_extracted=... status=processed`).
  - `model_used == "llama3.1"`.

- [ ] **Step 4: Run a real end-to-end pipeline call through `process_filing` against a real Postgres test database**

Using the same pattern as AGENT-07's live verification (a real Postgres container, a filing row with `raw_pdf_s3_key` pointing at ING-05's real stored test PDF, real chunking/embedding/extraction/summarization all running for real), confirm a genuine `briefs` row is written to the `briefs` table with all four fields populated and `filing_id` matching the test filing. Clean up the test row afterward.

- [ ] **Step 5: Stop Ollama**

Per the project's no-unsupervised-background-services policy, stop Ollama once verification is done rather than leaving it running: `pkill ollama` (or however it was started).

- [ ] **Step 6: Update project memory**

Record the outcome of live verification (what was confirmed, any deviations found) — this happens outside the plan file, as a memory update once the ticket is complete.

---

## Self-Review Notes

- **Spec coverage:** All four AGENT-08 acceptance criteria are covered — (1) all four brief fields produced and written to `briefs` table (Tasks 1 & 3), (2) `executive_brief` 3-5 sentences enforced via `_count_sentences` + retry, not trusted from the model (Task 1), (3) each persona summary reflects its stated framing — CCO board-level/<50 words, Analyst obligations/deadlines bulleted list, Engineer shortest/non-narrative (Task 1's prompt + deterministic `_build_engineer_summary`), (4) ROUGE-L validation is explicitly out of scope for this ticket (EVAL-02's job per the ticket list) — not implemented here, matching AGENT-07's precedent of leaving eval-suite work to the EVAL-* tickets.
- **Placeholder scan:** No TBD/TODO markers; all code blocks are complete and copy-pasteable.
- **Type consistency:** `BriefSet` fields (`executive_brief`, `cco_summary`, `analyst_summary`, `engineer_summary`, `model_used`) used identically across Task 1 (construction) and Task 3 (persistence into `Brief` ORM fields of the same names) — confirmed against the already-existing `agents/state.py` and `models/brief.py` contents read directly from the repo.
