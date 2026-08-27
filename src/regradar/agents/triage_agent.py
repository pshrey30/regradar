"""The real Triage Agent — zero-shot classification via Hugging Face.

Replaces AGENT-01's passthrough triage_node stub with a call to HF's
hosted facebook/bart-large-mnli model, plus a deterministic keyword +
confidence heuristic for an initial risk_level. This module is pure —
no DB access — matching every other node in agents/graph.py; DB
persistence happens in workers/pipeline_tasks.py.
"""

import json
import logging
import time

import httpx
from openai import OpenAI
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

SEVERITY_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

# Bumped whenever SPOT_CHECK_SYSTEM_PROMPT/SPOT_CHECK_USER_PROMPT_TEMPLATE change
# meaningfully — EVAL-05's LangSmith Prompt Hub push tags each pushed version
# with this identifier.
PROMPT_VERSION = "triage-spot-check-v1"

SPOT_CHECK_SYSTEM_PROMPT = "You are a regulatory filing classifier. Respond with strict JSON only."
SPOT_CHECK_USER_PROMPT_TEMPLATE = (
    'Classify this filing into one of ["financial", "clinical", "environmental", "other"], '
    'and independently assign a risk_level ("low", "medium", "high", "critical") with a brief '
    'reasoning. Text: "{text}" '
    'Respond as JSON: {{"domain": ..., "risk_level": ..., "reasoning": ...}}'
)


class TriageClassificationError(Exception):
    """Raised when HF classification fails after one retry."""


class ClassificationResult(BaseModel):
    domain: FilingDomain
    confidence: float
    raw_scores: dict[str, float]


class SpotCheckResult(BaseModel):
    domain: FilingDomain
    risk_level: RiskLevel
    reasoning: str


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
        content = response.choices[0].message.content or ""
        parsed = json.loads(content)
        return SpotCheckResult(
            domain=FilingDomain(parsed["domain"]),
            risk_level=RiskLevel(parsed["risk_level"]),
            reasoning=parsed["reasoning"],
        )
    except Exception as exc:  # noqa: BLE001 — any failure here must degrade, not raise
        logger.warning("Spot-check classification failed: %s", exc)
        return None


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
