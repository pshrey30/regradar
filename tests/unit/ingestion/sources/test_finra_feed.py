"""Unit tests for the FINRA Reg SHO Threshold List connector."""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from regradar.ingestion.sources import finra_feed
from regradar.models.enums import FilingDomain, FilingSource
from regradar.models.source_config import SourceConfig

FIXTURES_DIR = Path(__file__).parents[3] / "fixtures" / "sample_filings"

FAKE_TOKEN_RESPONSE = {"access_token": "fake-test-token", "token_type": "Bearer", "expires_in": 3600}


def _make_source_config() -> SourceConfig:
    return SourceConfig(
        id=uuid.uuid4(),
        source=FilingSource.FINRA,
        domains=[FilingDomain.FINANCIAL.value],
        is_active=True,
        poll_interval_seconds=300,
    )


@asynccontextmanager
async def _noop_nested_transaction():
    yield


def _make_mock_db(existing_ids: set[str] | None = None) -> AsyncMock:
    db = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = list(existing_ids or [])
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result
    db.execute = AsyncMock(return_value=execute_result)
    db.begin_nested = MagicMock(side_effect=lambda: _noop_nested_transaction())
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


def _load_fixture(name: str) -> list[dict]:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def _reset_token_cache():
    finra_feed._token_cache.clear()
    yield
    finra_feed._token_cache.clear()


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_SECRET_KEY", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "test")
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "RegRadar/1.0 (test@example.com)")
    monkeypatch.setenv("FINRA_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("FINRA_CLIENT_SECRET", "test-client-secret")
    finra_feed.get_settings.cache_clear()
    yield
    finra_feed.get_settings.cache_clear()


def _mock_post_factory(data_rows: list[dict] | None, data_status: int = 200):
    def _mock_post(url, **kwargs):
        if url == finra_feed.FINRA_TOKEN_URL:
            return MagicMock(status_code=200, json=lambda: FAKE_TOKEN_RESPONSE)
        if url == finra_feed.FINRA_THRESHOLD_LIST_URL:
            return MagicMock(
                status_code=data_status,
                json=lambda: data_rows if data_rows is not None else [],
                text=json.dumps(data_rows) if data_rows is not None else "[]",
            )
        raise AssertionError(f"Unexpected URL: {url}")

    return _mock_post


async def test_normal_response_returns_and_inserts_all_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _load_fixture("finra_threshold_list_normal.json")
    monkeypatch.setattr(finra_feed.httpx, "post", _mock_post_factory(rows))

    db = _make_mock_db(existing_ids=set())
    result = await finra_feed.poll_finra(
        _make_source_config(), db, report_date=date(2026, 8, 4)
    )

    assert len(result) == 2
    ids = {r.source_document_id for r in result}
    assert "reg-sho-threshold-2026-08-04-SAMPA" in ids
    assert "reg-sho-threshold-2026-08-04-SAMPB" in ids
    assert db.add.call_count == 2
    db.commit.assert_awaited_once()


async def test_empty_response_returns_empty_list_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _load_fixture("finra_threshold_list_empty.json")
    monkeypatch.setattr(finra_feed.httpx, "post", _mock_post_factory(rows))

    db = _make_mock_db()
    result = await finra_feed.poll_finra(
        _make_source_config(), db, report_date=date(2026, 8, 4)
    )

    assert result == []
    db.add.assert_not_called()


async def test_already_existing_row_is_not_reinserted(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _load_fixture("finra_threshold_list_normal.json")
    monkeypatch.setattr(finra_feed.httpx, "post", _mock_post_factory(rows))

    db = _make_mock_db(
        existing_ids={
            "reg-sho-threshold-2026-08-04-SAMPA",
            "reg-sho-threshold-2026-08-04-SAMPB",
        }
    )
    result = await finra_feed.poll_finra(
        _make_source_config(), db, report_date=date(2026, 8, 4)
    )

    assert result == []
    db.add.assert_not_called()


async def test_no_credentials_configured_returns_empty_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FINRA_CLIENT_ID", raising=False)
    monkeypatch.delenv("FINRA_CLIENT_SECRET", raising=False)
    finra_feed.get_settings.cache_clear()

    db = _make_mock_db()
    result = await finra_feed.poll_finra(_make_source_config(), db)

    assert result == []
    db.execute.assert_not_called()


async def test_oauth_failure_returns_empty_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _mock_post(url, **kwargs):
        return MagicMock(status_code=401, json=lambda: {"error": "invalid_client"})

    monkeypatch.setattr(finra_feed.httpx, "post", _mock_post)

    db = _make_mock_db()
    result = await finra_feed.poll_finra(_make_source_config(), db)

    assert result == []
    db.execute.assert_not_called()


async def test_data_request_failure_returns_empty_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finra_feed.httpx, "post", _mock_post_factory([], data_status=500))

    db = _make_mock_db()
    result = await finra_feed.poll_finra(
        _make_source_config(), db, report_date=date(2026, 8, 4)
    )

    assert result == []
    db.execute.assert_not_called()


async def test_network_error_returns_empty_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args, **kwargs):
        raise httpx.ConnectError("simulated network failure")

    monkeypatch.setattr(finra_feed.httpx, "post", _raise)

    db = _make_mock_db()
    result = await finra_feed.poll_finra(_make_source_config(), db)

    assert result == []


def test_token_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = {"n": 0}

    def _mock_post(url, **kwargs):
        call_count["n"] += 1
        return MagicMock(status_code=200, json=lambda: FAKE_TOKEN_RESPONSE)

    monkeypatch.setattr(finra_feed.httpx, "post", _mock_post)

    token1 = finra_feed._get_access_token("id", "secret")
    token2 = finra_feed._get_access_token("id", "secret")

    assert token1 == token2 == "fake-test-token"
    assert call_count["n"] == 1
