"""Unit tests for the FDA RSS connector, using local fixture feed files."""

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from regradar.ingestion.sources import fda_rss
from regradar.models.enums import FilingDomain, FilingSource
from regradar.models.source_config import SourceConfig

FIXTURES_DIR = Path(__file__).parents[3] / "fixtures" / "sample_filings"


def _make_source_config(feed_url: str | None) -> SourceConfig:
    return SourceConfig(
        id=uuid.uuid4(),
        source=FilingSource.FDA,
        domains=[FilingDomain.CLINICAL.value],
        feed_url=feed_url,
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


async def test_normal_feed_returns_and_inserts_all_items() -> None:
    source_config = _make_source_config(str(FIXTURES_DIR / "fda_feed_normal.xml"))
    db = _make_mock_db(existing_ids=set())

    result = await fda_rss.poll_fda_rss(source_config, db)

    assert len(result) == 2
    ids = {r.source_document_id for r in result}
    assert "http://www.fda.gov/drugs/sample-guidance-update-1" in ids
    assert "http://www.fda.gov/drugs/sample-warning-letter-2" in ids
    assert db.add.call_count == 2
    db.commit.assert_awaited_once()


async def test_empty_feed_returns_empty_list_without_crashing() -> None:
    source_config = _make_source_config(str(FIXTURES_DIR / "fda_feed_empty.xml"))
    db = _make_mock_db()

    result = await fda_rss.poll_fda_rss(source_config, db)

    assert result == []
    db.add.assert_not_called()


async def test_malformed_feed_returns_whatever_parsed_without_crashing() -> None:
    source_config = _make_source_config(str(FIXTURES_DIR / "fda_feed_malformed.xml"))
    db = _make_mock_db(existing_ids=set())

    # Should not raise, regardless of how many (if any) entries survive the
    # malformed XML.
    result = await fda_rss.poll_fda_rss(source_config, db)
    assert isinstance(result, list)


async def test_already_existing_item_is_not_reinserted() -> None:
    source_config = _make_source_config(str(FIXTURES_DIR / "fda_feed_normal.xml"))
    db = _make_mock_db(
        existing_ids={
            "http://www.fda.gov/drugs/sample-guidance-update-1",
            "http://www.fda.gov/drugs/sample-warning-letter-2",
        }
    )

    result = await fda_rss.poll_fda_rss(source_config, db)

    assert result == []
    db.add.assert_not_called()


async def test_no_feed_url_configured_returns_empty_without_crashing() -> None:
    source_config = _make_source_config(feed_url=None)
    db = _make_mock_db()

    result = await fda_rss.poll_fda_rss(source_config, db)

    assert result == []
    db.execute.assert_not_called()


async def test_unreachable_feed_returns_empty_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args, **kwargs):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(fda_rss.feedparser, "parse", _raise)

    source_config = _make_source_config("https://example.invalid/does-not-exist.xml")
    db = _make_mock_db()

    result = await fda_rss.poll_fda_rss(source_config, db)

    assert result == []
    db.execute.assert_not_called()
