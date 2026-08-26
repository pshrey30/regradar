"""Shared insertion logic for every ingestion connector.

All three connectors (sec_edgar.py, fda_rss.py, finra_feed.py) independently
duplicated the same begin_nested()/add()/IntegrityError-catch pattern. This
is that logic, extracted once — and the hook point sec_edgar.py's PDF-intake
wiring needs, since intake_pdf() requires a real, committed Filing.id.
"""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.ingestion.types import NewFiling
from regradar.models.enums import FilingStatus
from regradar.models.filing import Filing
from regradar.models.source_config import SourceConfig


async def insert_new_filing(
    db: AsyncSession, source_config: SourceConfig, candidate: NewFiling
) -> Filing | None:
    """Insert one new Filing row. Returns the row (with a real id) on
    success, or None if another poller already inserted this
    source_document_id first (IntegrityError race) — the unique
    constraint is the real guarantee; this is just how a connector finds
    out it lost that race.

    The filing inherits organization_id from the source_config that
    discovered it (SEC-05) — the connector-level org boundary."""
    filing = Filing(
        organization_id=source_config.organization_id,
        source=source_config.source,
        source_document_id=candidate.source_document_id,
        entity_name=candidate.entity_name,
        filing_type=candidate.filing_type,
        filing_url=candidate.filing_url,
        published_at=candidate.published_at,
        ingested_at=datetime.now(UTC),
        status=FilingStatus.INGESTED,
    )
    try:
        async with db.begin_nested():
            db.add(filing)
    except IntegrityError:
        return None
    return filing
