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
