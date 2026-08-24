"""Unit tests for the SEC EDGAR connector: HTTP handling, not real DB access."""

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from regradar.ingestion.sources import sec_edgar
from regradar.models.enums import FilingDomain, FilingSource
from regradar.models.source_config import SourceConfig

SAMPLE_FEED = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Latest Filings</title>
<entry>
<title>8-K - Acme Capital Inc. (0000320193) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/0000320193-26-000018-index.htm"/>
<summary type="html">Filed: 2026-07-30</summary>
<updated>2026-07-30T08:53:16-04:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
<id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000018</id>
</entry>
</feed>
"""


def _make_source_config() -> SourceConfig:
    return SourceConfig(
        id=uuid.uuid4(),
        source=FilingSource.SEC,
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


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    sec_edgar._rate_limiter = None
    yield
    sec_edgar._rate_limiter = None


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
    sec_edgar.get_settings.cache_clear()
    yield
    sec_edgar.get_settings.cache_clear()


async def test_normal_new_filing_is_returned_and_inserted(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_response = MagicMock(status_code=200, text=SAMPLE_FEED)
    monkeypatch.setattr(sec_edgar.httpx, "get", MagicMock(return_value=mock_response))

    db = _make_mock_db(existing_ids=set())
    result = await sec_edgar.poll_edgar(_make_source_config(), db)

    assert len(result) == 1
    assert result[0].source_document_id == "0000320193-26-000018"
    assert result[0].entity_name == "Acme Capital Inc."
    assert result[0].filing_type == "8-K"
    db.add.assert_called_once()
    db.commit.assert_awaited_once()


async def test_already_existing_filing_is_not_reinserted(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_response = MagicMock(status_code=200, text=SAMPLE_FEED)
    monkeypatch.setattr(sec_edgar.httpx, "get", MagicMock(return_value=mock_response))

    db = _make_mock_db(existing_ids={"0000320193-26-000018"})
    result = await sec_edgar.poll_edgar(_make_source_config(), db)

    assert result == []
    db.add.assert_not_called()


async def test_rate_limit_response_returns_empty_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MagicMock(status_code=429, text="")
    monkeypatch.setattr(sec_edgar.httpx, "get", MagicMock(return_value=mock_response))

    db = _make_mock_db()
    result = await sec_edgar.poll_edgar(_make_source_config(), db)

    assert result == []
    db.execute.assert_not_called()


async def test_connection_error_returns_empty_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(sec_edgar.httpx, "get", _raise_connect_error)

    db = _make_mock_db()
    result = await sec_edgar.poll_edgar(_make_source_config(), db)

    assert result == []
    db.execute.assert_not_called()


def test_rate_limiter_sleeps_between_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(sec_edgar.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    fake_clock = iter([100.0, 100.05, 100.05])
    monkeypatch.setattr(sec_edgar.time, "monotonic", lambda: next(fake_clock))

    limiter = sec_edgar._EdgarRateLimiter(requests_per_sec=10)
    limiter.wait()
    limiter.wait()

    assert len(sleep_calls) == 1
    assert sleep_calls[0] > 0


SAMPLE_SUBMISSIONS_JSON = {
    "filings": {
        "recent": {
            "accessionNumber": ["0000320193-25-000079", "0001140361-26-033928"],
            "form": ["10-K", "4"],
            "primaryDocument": ["aapl-20250927.htm", "xslF345X06/form4.xml"],
        }
    }
}


def _mock_async_client(response=None, side_effect=None) -> MagicMock:
    client = AsyncMock()
    if side_effect is not None:
        client.get.side_effect = side_effect
    else:
        client.get.return_value = response
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    return client


async def test_resolve_primary_document_url_returns_real_url(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = SAMPLE_SUBMISSIONS_JSON
    monkeypatch.setattr(
        sec_edgar.httpx, "AsyncClient", MagicMock(return_value=_mock_async_client(mock_response))
    )

    result = await sec_edgar._resolve_primary_document_url(
        cik="320193", accession_number="0000320193-25-000079", user_agent="test-agent"
    )

    assert result == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm"
    )


async def test_resolve_primary_document_url_returns_none_when_accession_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = SAMPLE_SUBMISSIONS_JSON
    monkeypatch.setattr(
        sec_edgar.httpx, "AsyncClient", MagicMock(return_value=_mock_async_client(mock_response))
    )

    result = await sec_edgar._resolve_primary_document_url(
        cik="320193", accession_number="9999999999-99-999999", user_agent="test-agent"
    )

    assert result is None


async def test_resolve_primary_document_url_returns_none_on_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sec_edgar.httpx,
        "AsyncClient",
        MagicMock(
            return_value=_mock_async_client(side_effect=sec_edgar.httpx.RequestError("timeout"))
        ),
    )

    result = await sec_edgar._resolve_primary_document_url(
        cik="320193", accession_number="0000320193-25-000079", user_agent="test-agent"
    )

    assert result is None


async def test_resolve_primary_document_url_returns_none_on_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 404
    monkeypatch.setattr(
        sec_edgar.httpx, "AsyncClient", MagicMock(return_value=_mock_async_client(mock_response))
    )

    result = await sec_edgar._resolve_primary_document_url(
        cik="320193", accession_number="0000320193-25-000079", user_agent="test-agent"
    )

    assert result is None


def test_extract_cik_from_filing_url_parses_data_segment() -> None:
    url = (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/"
        "0000320193-26-000018-index.htm"
    )
    assert sec_edgar._extract_cik_from_filing_url(url) == "320193"


def test_extract_cik_from_filing_url_returns_none_for_unrecognized_url() -> None:
    assert sec_edgar._extract_cik_from_filing_url("https://example.com/nothing") is None
