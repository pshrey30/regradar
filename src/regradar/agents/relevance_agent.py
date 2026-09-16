"""The Relevance Agent (ORG-11) — scores and explains how much a filing
matters to the *specific* receiving organization's business, not just how
objectively severe the filing is.

relevance_node is a plain sync function — no DB access, matching every
node in agents/graph.py except retrieve_node/deliver_node. Unlike
analyze_node (which leaves state.extraction at None on failure, signalling
needs_review), this node NEVER leaves state.relevance at None —
pipeline_tasks.py's priority_score computation and deliver_node's
role-routed alert content both depend on it unconditionally, so a failure
(LLM error, malformed JSON after retry, or no extraction to reason about)
degrades to a neutral, clearly-labeled default instead of blocking the
pipeline — the same "never raises out of the node" convention
triage_agent.py's spot_check_classification established.
"""

import json
import logging

from openai import APIConnectionError, InternalServerError, OpenAI, RateLimitError

from regradar.agents.state import OrgProfileSnapshot, PipelineState, RelevanceResult
from regradar.agents.triage_agent import SEVERITY_ORDER
from regradar.llm_routing.tiered_router import ModelChoice, build_client, select_model
from regradar.models.enums import RiskLevel

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
_SEVERITY_MAX = max(SEVERITY_ORDER.values())

_NEUTRAL_RATIONALE = (
    "Relevance to your organization's specific business could not be determined "
    "automatically for this filing; review it directly."
)
_NEUTRAL_ACTION = "Review this filing manually to assess impact on your organization."

# Bumped whenever RELEVANCE_SYSTEM_PROMPT changes meaningfully — matches
# the PROMPT_VERSION convention in triage_agent.py/analysis_agent.py.
PROMPT_VERSION = "relevance-v1"

RELEVANCE_SYSTEM_PROMPT = (
    "You are a regulatory risk analyst for a specific organization. Given that "
    "organization's business profile and a filing's extracted obligations, risk "
    "flags, affected products, key entities, and competitor mentions, assess how "
    "relevant and impactful this filing is to THIS organization specifically — not "
    "how severe the filing is in general. Respond with strict JSON only, matching "
    "the required schema exactly."
)

RELEVANCE_RETRY_SUFFIX = (
    " The previous response was invalid — every field is required. Respond with "
    "strict, schema-conformant JSON only."
)

RELEVANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance_score": {"type": "number"},
        "matched_signals": {
            "type": "object",
            "properties": {
                "watchlist_entities": {"type": "array", "items": {"type": "string"}},
                "products": {"type": "array", "items": {"type": "string"}},
                "industry_alignment": {"type": "string"},
            },
            "required": ["watchlist_entities", "products", "industry_alignment"],
        },
        "rationale": {"type": "string"},
        "recommended_action": {"type": "string"},
    },
    "required": ["relevance_score", "matched_signals", "rationale", "recommended_action"],
}


class RelevanceError(Exception):
    """Raised internally when scoring fails validation after retry — caught
    by relevance_node, never propagates out of it."""


def _neutral_result() -> RelevanceResult:
    return RelevanceResult(
        relevance_score=0.5,
        matched_signals={},
        rationale=_NEUTRAL_RATIONALE,
        recommended_action=_NEUTRAL_ACTION,
        model_used=None,
    )


def _get_llm_client(risk_level: RiskLevel | None) -> tuple[OpenAI, str, ModelChoice]:
    choice = select_model(risk_level, task="relevance")
    return build_client(choice), choice.model, choice


def _build_relevance_prompt(state: PipelineState) -> str:
    org_profile = state.org_profile or OrgProfileSnapshot()
    extraction = state.extraction
    return (
        f"Organization profile:\n"
        f"- Industry: {org_profile.industry or 'not specified'}\n"
        f"- Business description: {org_profile.business_description or 'not specified'}\n"
        f"- Watchlist entities: {', '.join(org_profile.watchlist_entities) or 'none'}\n"
        f"- Products/services: {', '.join(org_profile.products) or 'none'}\n"
        f"- Risk priorities (in order): {', '.join(org_profile.risk_priorities) or 'none'}\n\n"
        f"Filing domain: {state.domain.value if state.domain else 'unknown'}\n"
        f"Filing risk level: {state.risk_level.value if state.risk_level else 'unknown'}\n"
        f"Risk flags: {', '.join(extraction.risk_flags) if extraction else 'none'}\n"
        f"Affected products: {', '.join(extraction.affected_products) if extraction else 'none'}\n"
        f"Key entities: {', '.join(extraction.key_entities) if extraction else 'none'}\n"
        f"Competitor mentions: {', '.join(extraction.competitor_mentions) if extraction else 'none'}\n"
        f"Obligations: {extraction.obligations if extraction else []}"
    )


def _call_relevance_model(client: OpenAI, model: str, prompt: str, strict_retry: bool) -> dict:
    system_prompt = RELEVANCE_SYSTEM_PROMPT + (RELEVANCE_RETRY_SUFFIX if strict_retry else "")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "relevance", "schema": RELEVANCE_SCHEMA, "strict": True},
        },
        temperature=0,
    )
    content = response.choices[0].message.content or ""
    return json.loads(content)


def _validate_relevance(parsed: dict) -> None:
    try:
        for key in RELEVANCE_SCHEMA["required"]:
            if key not in parsed:
                raise RelevanceError(f"Missing required field: {key}")
        if not isinstance(parsed["relevance_score"], (int, float)):
            raise RelevanceError(f"relevance_score must be numeric, got {parsed['relevance_score']!r}")
        if not isinstance(parsed["matched_signals"], dict):
            raise RelevanceError("matched_signals must be an object")
        if not isinstance(parsed["rationale"], str) or not isinstance(parsed["recommended_action"], str):
            raise RelevanceError("rationale and recommended_action must be strings")
    except RelevanceError:
        raise
    except Exception as exc:
        raise RelevanceError(f"Malformed relevance response: {exc}") from exc


def compute_priority_score(risk_level: RiskLevel, relevance_score: float) -> float:
    """Equal-weighted blend of objective severity and org-specific
    relevance, on a 0-100 scale. An objectively CRITICAL filing with zero
    org relevance and a LOW-risk filing with perfect org relevance land at
    the same midpoint score — both get surfaced, neither dominates."""
    severity_component = SEVERITY_ORDER[risk_level] / _SEVERITY_MAX
    return round(100 * (0.5 * severity_component + 0.5 * relevance_score), 1)


def relevance_node(state: PipelineState) -> PipelineState:
    """Builds a prompt from org_profile + extraction, calls the model,
    validates+retries once. Falls back to a neutral default — never
    None — if state.extraction is missing, or if the LLM call/parse/
    validation fails twice."""
    if state.extraction is None:
        logger.warning(
            "No extraction available for filing %s; using neutral relevance default", state.filing_id
        )
        return state.model_copy(update={"relevance": _neutral_result()})

    prompt = _build_relevance_prompt(state)
    client, model_name, choice = _get_llm_client(state.risk_level)

    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            parsed = _call_relevance_model(client, model_name, prompt, strict_retry=attempt > 0)
            _validate_relevance(parsed)
            relevance = RelevanceResult(
                relevance_score=float(parsed["relevance_score"]),
                matched_signals=parsed["matched_signals"],
                rationale=parsed["rationale"],
                recommended_action=parsed["recommended_action"],
                model_used=model_name,
            )
            return state.model_copy(update={"relevance": relevance})
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "Relevance scoring attempt %d failed for filing %s: %s",
                attempt + 1,
                state.filing_id,
                exc,
            )

    logger.error(
        "Relevance scoring failed for filing %s after %d attempts: %s; using neutral default",
        state.filing_id,
        MAX_ATTEMPTS,
        last_error,
    )
    return state.model_copy(update={"relevance": _neutral_result()})
