"""SEC EDGAR ingestion connector.

Polls EDGAR's "current filings" Atom feed — verified against the live API
(2026-07-31) to be the correct mechanism for "what filings were just
published, across every company." Two other endpoints are sometimes
suggested for this and don't actually work for it:

- The submissions API (data.sec.gov/submissions/CIK##########.json) is
  scoped to one company by CIK. There's no "give me everyone's new
  filings" mode — you'd need a pre-built watchlist of every CIK, which
  this project doesn't maintain.
- The full-text search API (efts.sec.gov/LATEST/search-index) requires a
  keyword query parameter (`q=`). It's a search engine, not a listing —
  there's no way to ask it for "everything, no keyword filter."

The current-filings feed (sec.gov/cgi-bin/browse-edgar?action=getcurrent)
needs neither: it's EDGAR's own mechanism for "show me the newest filings
right now," and returns entity name, form type, accession number, and a
filing index URL per entry, no CIK or search term required. Also: it
returns no ETag/Last-Modified headers (verified empirically), so dedup
here is DB-driven (source_document_id uniqueness) rather than
conditional-GET based.
"""

import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.core.config import get_settings
from regradar.ingestion.types import NewFiling
from regradar.models.enums import FilingSource, FilingStatus
from regradar.models.filing import Filing
from regradar.models.source_config import SourceConfig

EDGAR_CURRENT_FEED_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
ATOM_NS = "{http://www.w3.org/2005/Atom}"


class _EdgarRateLimiter:
    """Sleep-based limiter respecting SEC_EDGAR_RATE_LIMIT_PER_SEC."""

    def __init__(self, requests_per_sec: int) -> None:
        self._min_interval = 1.0 / requests_per_sec if requests_per_sec > 0 else 0.0
        self._last_request_at: float | None = None

    def wait(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self._min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()


_rate_limiter: _EdgarRateLimiter | None = None


def _get_rate_limiter() -> _EdgarRateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = _EdgarRateLimiter(get_settings().sec_edgar_rate_limit_per_sec)
    return _rate_limiter


def _parse_accession_number(entry_id: str) -> str | None:
    """Atom <id> looks like 'urn:tag:sec.gov,2008:accession-number=0001911501-26-000004'."""
    marker = "accession-number="
    idx = entry_id.find(marker)
    return entry_id[idx + len(marker) :] if idx != -1 else None


def _parse_entity_name(title: str) -> str:
    """Atom <title> looks like '10-Q - AGNC Investment Corp. (0001423689) (Filer)'."""
    remainder = title.split(" - ", 1)[1] if " - " in title else title
    paren_idx = remainder.find(" (")
    return remainder[:paren_idx].strip() if paren_idx != -1 else remainder.strip()


def _fetch_current_filings_feed(user_agent: str, count: int = 100) -> httpx.Response:
    _get_rate_limiter().wait()
    return httpx.get(
        EDGAR_CURRENT_FEED_URL,
        params={
            "action": "getcurrent",
            "type": "",
            "company": "",
            "dateb": "",
            "owner": "include",
            "count": str(count),
            "output": "atom",
        },
        headers={"User-Agent": user_agent},
        timeout=10.0,
    )


def _parse_feed(xml_text: str) -> list[NewFiling]:
    root = ET.fromstring(xml_text)
    candidates: list[NewFiling] = []

    for entry in root.findall(f"{ATOM_NS}entry"):
        entry_id_el = entry.find(f"{ATOM_NS}id")
        if entry_id_el is None or not entry_id_el.text:
            continue
        accession_number = _parse_accession_number(entry_id_el.text)
        if accession_number is None:
            continue

        title_el = entry.find(f"{ATOM_NS}title")
        entity_name = (
            _parse_entity_name(title_el.text) if title_el is not None and title_el.text else "Unknown"
        )

        category_el = entry.find(f"{ATOM_NS}category")
        filing_type = category_el.get("term", "unknown") if category_el is not None else "unknown"

        link_el = entry.find(f"{ATOM_NS}link")
        filing_url = link_el.get("href", "") if link_el is not None else ""

        updated_el = entry.find(f"{ATOM_NS}updated")
        published_at = (
            datetime.fromisoformat(updated_el.text)
            if updated_el is not None and updated_el.text
            else datetime.now(UTC)
        )

        candidates.append(
            NewFiling(
                source_document_id=accession_number,
                entity_name=entity_name,
                filing_type=filing_type,
                filing_url=filing_url,
                published_at=published_at,
            )
        )

    return candidates


async def poll_edgar(source_config: SourceConfig, db: AsyncSession) -> list[NewFiling]:
    """Poll EDGAR's current-filings feed, insert any genuinely new filings.

    Deviates from a plain `poll_edgar(source_config) -> list[NewFiling]`
    signature by taking a session directly — something has to supply one,
    and this keeps the function usable standalone (e.g. from a test or a
    future CLI command) rather than assuming a specific caller.

    Never raises on request failure — a down/rate-limited EDGAR should not
    take down the ingestion process; it just means zero new filings this
    cycle, which the caller (ING-04's Prefect flow) will retry later.
    """
    settings = get_settings()

    try:
        response = _fetch_current_filings_feed(settings.sec_edgar_user_agent)
    except httpx.RequestError:
        return []

    if response.status_code != 200:
        return []

    try:
        candidates = _parse_feed(response.text)
    except ET.ParseError:
        return []

    if not candidates:
        return []

    candidate_ids = [c.source_document_id for c in candidates]
    existing_ids_result = await db.execute(
        select(Filing.source_document_id).where(
            Filing.source == FilingSource.SEC,
            Filing.source_document_id.in_(candidate_ids),
        )
    )
    existing_ids = set(existing_ids_result.scalars().all())

    inserted: list[NewFiling] = []
    for candidate in candidates:
        if candidate.source_document_id in existing_ids:
            continue

        try:
            async with db.begin_nested():
                db.add(
                    Filing(
                        source=FilingSource.SEC,
                        source_document_id=candidate.source_document_id,
                        entity_name=candidate.entity_name,
                        filing_type=candidate.filing_type,
                        filing_url=candidate.filing_url,
                        published_at=candidate.published_at,
                        ingested_at=datetime.now(UTC),
                        status=FilingStatus.INGESTED,
                    )
                )
        except IntegrityError:
            # Lost a race to another poller inserting the same accession
            # number between our existence check and this insert — the
            # database-level unique constraint is the real guarantee here,
            # this pre-check is just an optimization to skip the round trip
            # for the common case.
            continue

        inserted.append(candidate)

    await db.commit()
    return inserted
