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
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("SEC_EDGAR_USER_AGENT", "RegRadar/1.0 (test@example.com)")

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
    assert high_choice.api_key.get_secret_value() == "ollama-local"

    low_choice = select_model_for_tier("low", task="analysis")
    assert low_choice.model == "llama3.2:1b"
    assert low_choice.base_url == "http://localhost:11434/v1"


def test_select_model_for_tier_real_mode_uses_groq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USE_LOCAL_LLM", "false")
    monkeypatch.setenv("TIER_HIGH_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("TIER_LOW_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test-key")
    monkeypatch.setenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

    high_choice = select_model_for_tier("high", task="analysis")
    assert high_choice.model == "openai/gpt-oss-120b"
    assert high_choice.base_url == "https://api.groq.com/openai/v1"
    assert high_choice.api_key.get_secret_value() == "gsk-test-key"

    low_choice = select_model_for_tier("low", task="analysis")
    assert low_choice.model == "openai/gpt-oss-20b"
    assert low_choice.base_url == "https://api.groq.com/openai/v1"
    assert low_choice.api_key.get_secret_value() == "gsk-test-key"


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


def test_select_model_accepts_relevance_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USE_LOCAL_LLM", "true")
    choice = select_model(RiskLevel.HIGH, task="relevance")
    assert choice.tier == "high"
