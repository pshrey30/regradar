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
from typing import cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONSchema

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


def _call_extraction_model(client: OpenAI, model: str, prompt: str, strict_retry: bool) -> dict:
    system_prompt = EXTRACTION_SYSTEM_PROMPT + (EXTRACTION_RETRY_SUFFIX if strict_retry else "")
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    response_format: ResponseFormatJSONSchema = {
        "type": "json_schema",
        "json_schema": {
            "name": "extraction",
            "schema": cast(dict[str, object], EXTRACTION_SCHEMA),
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


def _validate_extraction(parsed: dict, chunk_count: int) -> None:
    """Validate both presence AND type of every required field.

    Any malformed shape (wrong type, non-dict obligation entries, etc.)
    raises AnalysisError so analyze_node's except clause treats it as a
    validation failure — never lets a TypeError/AttributeError/KeyError
    escape and crash the pipeline.
    """
    try:
        for key in EXTRACTION_SCHEMA["required"]:
            if key not in parsed:
                raise AnalysisError(f"Missing required field: {key}")

        list_fields = (
            "obligations",
            "deadlines",
            "risk_flags",
            "affected_products",
            "key_entities",
            "competitor_mentions",
        )
        for field in list_fields:
            if not isinstance(parsed[field], list):
                raise AnalysisError(
                    f"Field {field!r} must be a list, got {type(parsed[field]).__name__}"
                )

        for obligation in parsed["obligations"]:
            if not isinstance(obligation, dict):
                raise AnalysisError(f"Invalid obligation entry: {obligation!r}")
            idx = obligation.get("source_chunk_index")
            if not isinstance(idx, int) or not (0 <= idx < chunk_count):
                raise AnalysisError(f"Invalid source_chunk_index: {idx!r}")
    except AnalysisError:
        raise
    except Exception as exc:
        # Any other malformed-shape error (e.g. .get() on a non-dict,
        # unexpected nesting) is still a validation failure, not a crash.
        raise AnalysisError(f"Malformed extraction response: {exc}") from exc


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
