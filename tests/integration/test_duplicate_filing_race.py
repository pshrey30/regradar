"""SEC-04 — proves the (source, source_document_id) unique constraint (migration
0002) genuinely prevents two near-simultaneous ingestion runs from creating two
rows for the same filing, at the database level, not just in application logic.

Unlike SEC-04's existing unit tests (test_common.py), which mock IntegrityError
to check insert_new_filing's own control flow, this fires two REAL concurrent
inserts — each in its own DB session/transaction, the way two separate
Prefect poller tasks (ing_04's poll_source) actually would — against a real
Postgres instance, and confirms Postgres itself, not application code, is
what enforces "only one row ever exists."
"""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from regradar.core.config import get_settings
from regradar.core.db import set_rls_context
from regradar.ingestion.sources._common import insert_new_filing
from regradar.ingestion.types import NewFiling
from regradar.models.enums import FilingSource
from regradar.models.filing import Filing

pytestmark = pytest.mark.asyncio

# Matches migration 0010's seeded default organization.
_DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
def _load_real_env_for_settings(monkeypatch: pytest.MonkeyPatch):
    """Same pattern as test_row_level_security.py: tests/conftest.py disables
    Settings' own .env reading process-wide, so this genuinely-live-infra
    test must populate os.environ directly instead."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    for key, value in dotenv_values(env_path).items():
        if value is not None:
            monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _insert_racing(source_document_id: str) -> Filing | None:
    """Opens its OWN fresh session (a separate connection, a separate
    transaction) — this is what actually makes it a real race: two truly
    independent database connections both trying to insert the same
    (source, source_document_id), the way two separate poller processes
    would, not two statements sharing one session/transaction."""
    settings = get_settings()
    engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        source_config = SimpleNamespace(source=FilingSource.SEC, organization_id=_DEFAULT_ORG_ID)
        result = await insert_new_filing(
            db,
            source_config,
            NewFiling(
                source_document_id=source_document_id,
                entity_name="Race Test Corp",
                filing_type="10-K",
                filing_url="https://example.com/race-test",
                published_at=datetime.now(UTC),
            ),
        )
        await db.commit()
    await engine.dispose()
    return result


async def test_two_concurrent_inserts_for_same_document_leave_exactly_one_row():
    doc_id = f"race-test-{uuid.uuid4()}"

    results = await asyncio.gather(
        _insert_racing(doc_id),
        _insert_racing(doc_id),
        return_exceptions=True,
    )

    # Neither concurrent attempt should have raised an unhandled exception —
    # the loser of the race must come back as a graceful None, per
    # insert_new_filing's own contract, not propagate an IntegrityError.
    for result in results:
        assert not isinstance(result, BaseException), f"insert_new_filing raised: {result!r}"

    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1, f"expected exactly one winner, got {len(winners)}: {results}"
    assert len(losers) == 1

    settings = get_settings()
    engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        rows = (
            await db.execute(
                select(Filing).where(
                    Filing.source == FilingSource.SEC, Filing.source_document_id == doc_id
                )
            )
        ).scalars().all()
        assert len(rows) == 1

        await db.execute(
            delete(Filing).where(
                Filing.source == FilingSource.SEC, Filing.source_document_id == doc_id
            )
        )
        await db.commit()
    await engine.dispose()
