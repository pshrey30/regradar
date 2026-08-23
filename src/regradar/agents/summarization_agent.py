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
