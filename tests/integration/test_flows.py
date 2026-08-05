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
