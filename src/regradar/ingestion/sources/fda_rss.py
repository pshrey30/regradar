"""FDA RSS ingestion connector.

Polls the RSS feed configured on a source_configs row (source_config.feed_url)
— verified against the live "What's New: Drugs" feed
(fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml) as the
representative FDA source for this ticket; other FDA feeds (MedWatch safety
alerts, recalls) follow the identical RSS 2.0 shape and can be added as
additional source_configs rows without any code change here.

Unlike EDGAR, FDA's feed needs no special User-Agent or auth, and does
return real ETag/Last-Modified headers — but dedup here still goes through
the database (source_document_id = the feed's GUID) rather than
conditional-GET, for the same reason as EDGAR: it's the one mechanism that
correctly survives a poller being down for a while and catching up, not
just "did anything change since my last single request."

feedparser (not manual XML parsing) handles both well-formed and malformed
RSS gracefully — a malformed feed still yields whatever entries it can
parse via `bozo`/`bozo_exception`, rather than raising, which is exactly
the graceful-degradation behavior this ticket asks for.

PDF intake (ingestion/pdf_intake.py, wired into sec_edgar.py) does not
apply here — this feed's entries link to HTML news pages on fda.gov, not
PDF documents; there's no document URL in the feed data to archive.
"""

import logging
from datetime import UTC, datetime
from typing import Any

import feedparser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.ingestion.sources._common import insert_new_filing
from regradar.ingestion.types import NewFiling
from regradar.models.enums import FilingSource
from regradar.models.filing import Filing
from regradar.models.source_config import SourceConfig

logger = logging.getLogger(__name__)

# FDA's "What's New" feeds don't categorize items into discrete form types
# the way EDGAR does (no <category>/tags element) — this is the best
# available generic label until a source-specific taxonomy is warranted.
DEFAULT_FDA_FILING_TYPE = "FDA Update"


def _extract_candidates(parsed: Any) -> list[NewFiling]:
    """Extract NewFiling candidates from an already-parsed feedparser result."""
    candidates: list[NewFiling] = []

    for entry in parsed.entries:
        guid = entry.get("id") or entry.get("link")
        if not guid:
            continue

        title = entry.get("title", "Untitled")
        link = entry.get("link", "")

        published_struct = entry.get("published_parsed")
        published_at = (
            datetime(
                published_struct.tm_year,
                published_struct.tm_mon,
                published_struct.tm_mday,
                published_struct.tm_hour,
                published_struct.tm_min,
                published_struct.tm_sec,
                tzinfo=UTC,
            )
            if published_struct is not None
            else datetime.now(UTC)
        )

        candidates.append(
            NewFiling(
                source_document_id=guid,
                entity_name=title,
                filing_type=DEFAULT_FDA_FILING_TYPE,
                filing_url=link,
                published_at=published_at,
            )
        )

    return candidates


async def poll_fda_rss(source_config: SourceConfig, db: AsyncSession) -> list[NewFiling]:
    """Poll the feed configured on source_config.feed_url, insert new items.

    Same signature-deviation rationale as poll_edgar: takes a session
    directly rather than assuming a specific caller supplies one.

    Never raises. No configured feed_url, a network failure, or a fully
    unparseable response all just mean zero new filings this cycle.
    """
    if not source_config.feed_url:
        logger.warning("source_config %s has no feed_url configured, skipping", source_config.id)
        return []

    try:
        parsed = feedparser.parse(source_config.feed_url)
    except Exception:
        logger.exception("Failed to fetch/parse FDA RSS feed %s", source_config.feed_url)
        return []

    if parsed.bozo and not parsed.entries:
        logger.warning(
            "FDA RSS feed %s was unparseable and yielded no entries: %s",
            source_config.feed_url,
            getattr(parsed, "bozo_exception", "unknown parse error"),
        )
        return []

    if parsed.bozo:
        logger.warning(
            "FDA RSS feed %s had parsing issues, continuing with %d entries "
            "that did parse: %s",
            source_config.feed_url,
            len(parsed.entries),
            getattr(parsed, "bozo_exception", "unknown parse error"),
        )

    candidates = _extract_candidates(parsed)
    if not candidates:
        return []

    candidate_ids = [c.source_document_id for c in candidates]
    existing_ids_result = await db.execute(
        select(Filing.source_document_id).where(
            Filing.source == FilingSource.FDA,
            Filing.source_document_id.in_(candidate_ids),
        )
    )
    existing_ids = set(existing_ids_result.scalars().all())

    inserted: list[NewFiling] = []
    for candidate in candidates:
        if candidate.source_document_id in existing_ids:
            continue
        filing = await insert_new_filing(db, source_config, candidate)
        if filing is None:
            continue
        inserted.append(candidate)

    await db.commit()
    return inserted
