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
from pydantic import BaseModel, SecretStr

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
    api_key: SecretStr


def _tier_for_risk(risk_level: RiskLevel | None) -> Tier:
    """Unclassified (risk_level=None) defaults to the high tier — mirrors
    agents/graph.py's route_after_triage, which treats an unclassified
    filing the same as any non-low risk rather than defaulting to the
    cheaper path for a filing we haven't actually assessed yet."""
    settings = get_settings()
    threshold = settings.tier_routing_risk_threshold
    effective_risk = risk_level or RiskLevel.HIGH
    return "high" if _RISK_ORDER[effective_risk] >= _RISK_ORDER[threshold] else "low"


def select_model_for_tier(tier: Tier, task: Task) -> ModelChoice:
    """The actual provider/model selection for an already-decided tier."""
    settings = get_settings()
    if settings.use_local_llm:
        model = settings.local_llm_model if tier == "high" else settings.local_llm_low_model
        return ModelChoice(
            tier=tier,
            model=model,
            base_url=settings.local_llm_base_url,
            api_key=SecretStr("ollama-local"),
        )
    if tier == "high":
        return ModelChoice(
            tier="high",
            model=settings.tier_high_model,
            base_url=None,
            api_key=settings.openai_api_key,
        )
    return ModelChoice(
        tier="low",
        model=settings.tier_low_model,
        base_url="https://router.huggingface.co/v1",
        api_key=settings.huggingface_api_token,
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
    return OpenAI(base_url=choice.base_url, api_key=choice.api_key.get_secret_value())
