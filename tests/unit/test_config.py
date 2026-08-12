"""Tests for core.config.Settings — fail-fast validation, defaults, and secret masking."""

import pytest
from pydantic import ValidationError

from regradar.core.config import Settings

REQUIRED_ENV = {
    "APP_SECRET_KEY": "test-secret",
    "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/db",
    "REDIS_URL": "redis://localhost:6379/0",
    "S3_BUCKET_NAME": "test-bucket",
    "S3_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "AKIA_TEST",
    "AWS_SECRET_ACCESS_KEY": "test-aws-secret",
    "OPENAI_API_KEY": "sk-test",
    "HUGGINGFACE_API_TOKEN": "hf-test",
    "SEC_EDGAR_USER_AGENT": "RegRadar/1.0 (test@example.com)",
}


def test_missing_required_field_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_all_required_fields_present_loads_with_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.env == "development"
    assert settings.tier_high_model == "gpt-4o"
    assert settings.tier_low_model == "granite-13b"
    assert settings.database_pool_size == 10
    assert settings.api_rate_limit_per_minute_default == 60
    assert settings.use_local_llm is False
    assert settings.use_local_hf_inference is False
    assert settings.local_llm_base_url == "http://localhost:11434/v1"
    assert settings.local_llm_model == "llama3.1"
    assert settings.classification_confidence_threshold == 0.75


def test_sec_edgar_user_agent_requires_contact_email(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "RegRadar/1.0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_secrets_never_appear_in_repr_or_str(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    dump = repr(settings) + str(settings)
    assert "test-secret" not in dump
    assert "test-aws-secret" not in dump
    assert "sk-test" not in dump
    assert "hf-test" not in dump
