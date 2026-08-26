"""Prefect flow: poll every active regulatory source, on demand or on a schedule.

Two ways to run this, deliberately kept separate:

- `poll_all_sources()` — a single on-demand cycle. This is what
  `python -m regradar.cli poll-once` calls. Use this for regular
  development/testing; it starts, runs one cycle across all active
  sources, and exits.
- `serve_scheduled()` — starts a long-lived process that re-runs
  `poll_all_sources()` every `INGESTION_POLL_INTERVAL_SECONDS` (default
  300s, matching the PRD's <5 min ingestion-latency goal). Only run this
  when you actually want continuous polling — e.g. while demoing or
  recording — not as a background service left running indefinitely.
  There's no live deployment for this project, so nothing depends on it
  running unattended.

last_etag is deliberately never updated here: none of the three
connectors (EDGAR, FDA, FINRA) produce a meaningful ETag value in their
current design — EDGAR's feed returns none, FDA's dedup is DB-driven
rather than conditional-GET based, and FINRA's data is OAuth + date-
partitioned rather than per-item. See each connector's module docstring
for the specific reasoning. last_polled_at is real and is updated after
every successful run.
"""

import logging
import uuid
from datetime import UTC, datetime

from prefect import flow, task
from prefect.tasks import exponential_backoff
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.core.config import get_settings
from regradar.core.db import get_session_factory, set_rls_context
from regradar.ingestion.sources.fda_rss import poll_fda_rss
from regradar.ingestion.sources.finra_feed import poll_finra
from regradar.ingestion.sources.sec_edgar import poll_edgar
from regradar.ingestion.types import NewFiling
from regradar.models.enums import FilingSource
from regradar.models.source_config import SourceConfig

logger = logging.getLogger(__name__)

_CONNECTORS = {
    FilingSource.SEC: poll_edgar,
    FilingSource.FDA: poll_fda_rss,
    FilingSource.FINRA: poll_finra,
}


@task(
    name="poll-source",
    retries=3,
    retry_delay_seconds=exponential_backoff(backoff_factor=10),
    retry_jitter_factor=0.5,
)
async def poll_source(source_config_id: uuid.UUID, source: FilingSource) -> list[NewFiling]:
    """Poll one source_configs row's connector in its own DB session.

    A fresh session per task (rather than sharing one across all sources)
    means one source's failure can't leave a shared transaction in a bad
    state for the others.
    """
    connector = _CONNECTORS[source]
    session_factory = get_session_factory()
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        source_config = await db.get(SourceConfig, source_config_id)
        if source_config is None:
            logger.warning("source_config %s no longer exists, skipping", source_config_id)
            return []

        new_filings = await connector(source_config, db)

        source_config.last_polled_at = datetime.now(UTC)
        await db.commit()
        return new_filings


async def _get_active_source_configs(db: AsyncSession) -> list[SourceConfig]:
    result = await db.execute(select(SourceConfig).where(SourceConfig.is_active.is_(True)))
    return list(result.scalars().all())


@flow(name="poll-all-sources")
async def poll_all_sources() -> dict[str, int]:
    """Run every active source_configs row's connector once.

    Returns a summary of {source_name: new_filing_count}, with -1 meaning
    that source failed after exhausting its retries. One source failing
    never stops the others from running — each is polled independently
    and any exception is caught and logged here, not propagated.
    """
    session_factory = get_session_factory()
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        active_configs = await _get_active_source_configs(db)

    if not active_configs:
        logger.warning("No active source_configs rows found — nothing to poll")
        return {}

    summary: dict[str, int] = {}
    for config in active_configs:
        source_name = config.source.value
        try:
            new_filings = await poll_source(config.id, config.source)
            summary[source_name] = len(new_filings)
            logger.info("Polled %s: %d new filing(s)", source_name, len(new_filings))
        except Exception:
            logger.exception("Polling %s failed after all retries", source_name)
            summary[source_name] = -1

    return summary


def serve_scheduled() -> None:
    """Start a long-lived process re-running poll_all_sources on an interval.

    Only run this when you want continuous polling. For a single on-demand
    run, use `python -m regradar.cli poll-once` instead.
    """
    settings = get_settings()
    poll_all_sources.serve(  # type: ignore[attr-defined]
        name="regradar-ingestion-scheduler",
        interval=settings.ingestion_poll_interval_seconds,
    )


if __name__ == "__main__":
    serve_scheduled()
