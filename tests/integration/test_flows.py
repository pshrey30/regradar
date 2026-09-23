"""Integration tests for the Prefect ingestion orchestration flow.

Uses a real database (orchestration logic queries/updates source_configs
directly) with the three connectors mocked — their own HTTP/parsing
behavior is already covered by ING-01/02/03's unit test suites. This
tests what flows.py is actually responsible for: dispatch, failure
isolation, and last_polled_at bookkeeping.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete

from regradar.core.db import get_session_factory
from regradar.ingestion import flows
from regradar.ingestion.types import NewFiling
from regradar.models.enums import FilingDomain, FilingSource
from regradar.models.filing import Filing
from regradar.models.source_config import SourceConfig


@pytest.fixture(autouse=True)
async def _clean_tables():
    """Isolate each test — these run against a real, shared database."""
    session_factory = get_session_factory()
    async with session_factory() as db:
        await db.execute(delete(Filing))
        await db.execute(delete(SourceConfig))
        await db.commit()
    yield
    session_factory = get_session_factory()
    async with session_factory() as db:
        await db.execute(delete(Filing))
        await db.execute(delete(SourceConfig))
        await db.commit()


async def _insert_source_config(db, source: FilingSource, is_active: bool = True) -> SourceConfig:
    config = SourceConfig(
        id=uuid.uuid4(),
        source=source,
        domains=[FilingDomain.FINANCIAL.value],
        is_active=is_active,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


async def _succeeds_with_one(source_config, db) -> list[NewFiling]:
    return [
        NewFiling(
            source_document_id=f"test-doc-{uuid.uuid4()}",
            entity_name="Test Co",
            filing_type="test",
            filing_url="https://example.com",
            published_at=datetime.now(UTC),
        )
    ]


async def _fails(source_config, db) -> list[NewFiling]:
    raise RuntimeError("simulated connector failure")


async def _succeeds_with_none(source_config, db) -> list[NewFiling]:
    return []


async def _succeeds_with_none_but_commits_like_a_real_connector(
    source_config, db
) -> list[NewFiling]:
    """Every real connector (sec_edgar.py/fda_rss.py/finra_feed.py) ends
    with its own unconditional `await db.commit()`, even when it found
    zero new filings — none of them special-case an empty result to skip
    it. This stub reproduces exactly that shape, which none of this
    file's other stubs do (they never commit at all), so this is the one
    that actually exercises the real bug: that commit reverts the
    transaction-scoped RLS GUC poll_source's own `set_rls_context` set,
    and poll_source's subsequent last_polled_at UPDATE then runs under a
    reverted role — RLS silently filters it to 0 matched rows, raising
    StaleDataError, on every single poll cycle, for every source."""
    await db.commit()
    return []


async def test_poll_all_sources_isolates_failures_and_updates_last_polled_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as db:
        sec_config = await _insert_source_config(db, FilingSource.SEC)
        fda_config = await _insert_source_config(db, FilingSource.FDA)
        finra_config = await _insert_source_config(db, FilingSource.FINRA)
        inactive_config = await _insert_source_config(db, FilingSource.SEC, is_active=False)

    monkeypatch.setitem(flows._CONNECTORS, FilingSource.SEC, _succeeds_with_one)
    monkeypatch.setitem(flows._CONNECTORS, FilingSource.FDA, _fails)
    monkeypatch.setitem(flows._CONNECTORS, FilingSource.FINRA, _succeeds_with_none)

    # Skip real retry/backoff delays for the deliberately-failing source in tests.
    monkeypatch.setattr(
        flows, "poll_source", flows.poll_source.with_options(retries=0, retry_delay_seconds=0)
    )

    summary = await flows.poll_all_sources()

    assert summary == {"SEC": 1, "FDA": -1, "FINRA": 0}

    async with session_factory() as db:
        refreshed_sec = await db.get(SourceConfig, sec_config.id)
        refreshed_fda = await db.get(SourceConfig, fda_config.id)
        refreshed_finra = await db.get(SourceConfig, finra_config.id)
        refreshed_inactive = await db.get(SourceConfig, inactive_config.id)

    assert refreshed_sec.last_polled_at is not None
    assert refreshed_fda.last_polled_at is None  # failed — never marked as polled
    assert refreshed_finra.last_polled_at is not None
    assert refreshed_inactive.last_polled_at is None  # inactive — never touched


async def test_poll_all_sources_returns_empty_summary_when_nothing_active() -> None:
    summary = await flows.poll_all_sources()
    assert summary == {}


async def test_poll_source_updates_last_polled_at_after_connector_commits_with_no_new_filings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for a real, live-verified bug (found during ORG-11/
    Groq-migration end-to-end testing against a real Postgres instance):
    every real connector's own trailing `db.commit()` reverts the
    transaction-scoped RLS GUC `poll_source` set at the top of its
    session, so its own `source_config.last_polled_at` UPDATE afterward
    was silently filtered to 0 matched rows by RLS, raising
    StaleDataError on every single poll cycle. Reproduced here with a
    connector stub that commits with zero new filings — the exact shape
    every real connector has and none of this file's other stubs do."""
    session_factory = get_session_factory()
    async with session_factory() as db:
        sec_config = await _insert_source_config(db, FilingSource.SEC)

    monkeypatch.setitem(
        flows._CONNECTORS, FilingSource.SEC, _succeeds_with_none_but_commits_like_a_real_connector
    )

    result = await flows.poll_source.fn(sec_config.id, FilingSource.SEC)

    assert result == []

    async with session_factory() as db:
        refreshed = await db.get(SourceConfig, sec_config.id)
    assert refreshed.last_polled_at is not None
