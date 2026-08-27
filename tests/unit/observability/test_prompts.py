"""Unit tests for EVAL-05's LangSmith Prompt Hub push — the real
langsmith.Client is mocked here; a real push against the actual LangSmith
service is verified separately, not by the automated unit suite (same
pattern as this project's other optional-external-service integrations)."""

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

from unittest.mock import MagicMock

import pytest

from regradar.core.config import get_settings
from regradar.observability import prompts as prompts_module


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_get_langsmith_client_returns_none_without_an_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

    assert prompts_module.get_langsmith_client() is None


def test_get_langsmith_client_returns_a_real_client_with_an_api_key(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-langsmith-key")
    mock_client_class = MagicMock()
    monkeypatch.setattr(prompts_module, "Client", mock_client_class)

    client = prompts_module.get_langsmith_client()

    assert client is mock_client_class.return_value
    mock_client_class.assert_called_once_with(api_key="test-langsmith-key")


def test_push_all_prompts_skips_everything_without_a_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(prompts_module, "get_langsmith_client", lambda: None)

    results = prompts_module.push_all_prompts()

    assert set(results.keys()) == {
        "regradar-search",
        "regradar-summarization",
        "regradar-extraction",
        "regradar-triage-spot-check",
    }
    assert all(url is None for url in results.values())


def test_push_all_prompts_pushes_every_registered_prompt(monkeypatch: pytest.MonkeyPatch):
    mock_client = MagicMock()
    mock_client.push_prompt.side_effect = lambda identifier, **kwargs: f"https://smith/{identifier}"
    monkeypatch.setattr(prompts_module, "get_langsmith_client", lambda: mock_client)

    results = prompts_module.push_all_prompts()

    assert results == {
        "regradar-search": "https://smith/regradar-search",
        "regradar-summarization": "https://smith/regradar-summarization",
        "regradar-extraction": "https://smith/regradar-extraction",
        "regradar-triage-spot-check": "https://smith/regradar-triage-spot-check",
    }
    assert mock_client.push_prompt.call_count == 4


def test_push_all_prompts_one_failure_does_not_block_the_others(monkeypatch: pytest.MonkeyPatch):
    mock_client = MagicMock()

    def _push(identifier, **kwargs):
        if identifier == "regradar-summarization":
            raise RuntimeError("LangSmith is down")
        return f"https://smith/{identifier}"

    mock_client.push_prompt.side_effect = _push
    monkeypatch.setattr(prompts_module, "get_langsmith_client", lambda: mock_client)

    results = prompts_module.push_all_prompts()

    assert results["regradar-summarization"] is None
    assert results["regradar-search"] == "https://smith/regradar-search"
    assert results["regradar-extraction"] == "https://smith/regradar-extraction"
    assert results["regradar-triage-spot-check"] == "https://smith/regradar-triage-spot-check"
    assert mock_client.push_prompt.call_count == 4


def test_prompt_registry_tags_each_push_with_its_local_version(monkeypatch: pytest.MonkeyPatch):
    mock_client = MagicMock()
    mock_client.push_prompt.return_value = "https://smith/whatever"
    monkeypatch.setattr(prompts_module, "get_langsmith_client", lambda: mock_client)

    prompts_module.push_all_prompts()

    tags_by_identifier = {
        call.args[0]: call.kwargs["tags"] for call in mock_client.push_prompt.call_args_list
    }
    assert tags_by_identifier["regradar-search"] == ["search-v1"]
    assert tags_by_identifier["regradar-summarization"] == ["summarization-v1"]
    assert tags_by_identifier["regradar-extraction"] == ["extraction-v1"]
    assert tags_by_identifier["regradar-triage-spot-check"] == ["triage-spot-check-v1"]
