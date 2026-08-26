"""FINRA ingestion connector — Reg SHO Threshold List.

FINRA's developer platform doesn't have anything resembling "new
regulatory notices" the way EDGAR/FDA do. Verified directly against the
live platform (2026-08-05): the only content resembling that (FINRA
Rulebook / FINRA Rulebook Notification) requires a paid Firm/Organization
credential ($1,650/month) — a free "Public" credential is explicitly
excluded, confirmed by the 403 response and the dataset's own documented
"API Credential Types: Firm, Organization" restriction. There is no
free path to that content.

This connector instead ingests the Reg SHO Threshold List
(group=otcMarket, dataset=thresholdList) — confirmed "Public"-accessible
— the closest genuinely-free FINRA data to a compliance-relevant signal:
securities that have failed to deliver for an extended period under
Regulation SHO / FINRA Rule 4320.

Structurally different from EDGAR/FDA: this is one list, republished each
trading day, not a stream of individually-identified new documents (no
per-item GUID or accession number exists). So "new" here means: each
security on a given day's list is treated as one filing, keyed by
(trade date, symbol) — re-polling the same day's list correctly inserts
nothing new, but a fresh day's list creates new rows even for symbols
that were already present the day before, since staying on the list is
itself meaningful, not noise to dedupe away.

The dataset is partitioned by tradeDate: FINRA's API requires an exact
EQUAL filter on it (confirmed empirically — a request with no filter
silently returns a stale/default partition, and sorting without an
equality filter on the partition key returns HTTP 400).

PDF intake (ingestion/pdf_intake.py, wired into sec_edgar.py) does not
apply here — this connector polls the Reg SHO Threshold List, a tabular
JSON dataset with no per-item document at all (every row shares the same
static API endpoint URL); there is no PDF to archive.
"""

import base64
import logging
import time
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.core.config import get_settings
from regradar.ingestion.sources._common import insert_new_filing
from regradar.ingestion.types import NewFiling
from regradar.models.enums import FilingSource
from regradar.models.filing import Filing
from regradar.models.source_config import SourceConfig

logger = logging.getLogger(__name__)

FINRA_TOKEN_URL = "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token"
FINRA_THRESHOLD_LIST_URL = "https://api.finra.org/data/group/otcMarket/name/thresholdList"
FILING_TYPE = "Reg SHO Threshold List"

# Cache the OAuth token in-memory — it's valid for ~12 hours, so fetching a
# fresh one on every poll would be wasteful and slower than necessary.
_token_cache: dict[str, str | float] = {}


def _get_access_token(client_id: str, client_secret: str) -> str | None:
    now = time.monotonic()
    cached_token = _token_cache.get("access_token")
    expires_at = _token_cache.get("expires_at")
    if isinstance(cached_token, str) and isinstance(expires_at, float) and now < expires_at:
        return cached_token

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        response = httpx.post(
            FINRA_TOKEN_URL,
            params={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {basic}"},
            timeout=15.0,
        )
    except httpx.RequestError:
        logger.exception("Failed to reach FINRA's OAuth token endpoint")
        return None

    if response.status_code != 200:
        logger.warning("FINRA OAuth token request failed: HTTP %d", response.status_code)
        return None

    body = response.json()
    access_token = body.get("access_token")
    if not access_token:
        logger.warning("FINRA OAuth response had no access_token")
        return None

    # Refresh a little early (60s buffer) rather than cutting it exactly at expiry.
    expires_in = float(body.get("expires_in", 0))
    _token_cache["access_token"] = access_token
    _token_cache["expires_at"] = now + max(expires_in - 60, 0)
    return access_token


def _fetch_threshold_list(access_token: str, report_date: date) -> httpx.Response:
    return httpx.post(
        FINRA_THRESHOLD_LIST_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        json={
            "compareFilters": [
                {
                    "compareType": "EQUAL",
                    "fieldName": "tradeDate",
                    "fieldValue": report_date.isoformat(),
                }
            ],
        },
        timeout=20.0,
    )


def _extract_candidates(rows: list[dict], report_date: date) -> list[NewFiling]:
    candidates: list[NewFiling] = []
    published_at = datetime(report_date.year, report_date.month, report_date.day, tzinfo=UTC)

    for row in rows:
        symbol = row.get("issueSymbolIdentifier")
        if not symbol:
            continue

        entity_name = row.get("issueName") or symbol
        source_document_id = f"reg-sho-threshold-{report_date.isoformat()}-{symbol}"

        candidates.append(
            NewFiling(
                source_document_id=source_document_id,
                entity_name=entity_name,
                filing_type=FILING_TYPE,
                filing_url=FINRA_THRESHOLD_LIST_URL,
                published_at=published_at,
            )
        )

    return candidates


async def poll_finra(
    source_config: SourceConfig,
    db: AsyncSession,
    report_date: date | None = None,
) -> list[NewFiling]:
    """Poll the Reg SHO Threshold List for a given trading day, insert new rows.

    Defaults report_date to today (UTC) — a scheduled poller (ING-04) would
    normally call this once per day without specifying a date. Never
    raises: missing credentials, an OAuth failure, a network error, or an
    empty/unpublished list for that date all just mean zero new filings.
    """
    settings = get_settings()
    if settings.finra_client_id is None or settings.finra_client_secret is None:
        logger.warning("FINRA_CLIENT_ID/FINRA_CLIENT_SECRET not configured, skipping")
        return []

    target_date = report_date or datetime.now(UTC).date()

    access_token = _get_access_token(
        settings.finra_client_id.get_secret_value(),
        settings.finra_client_secret.get_secret_value(),
    )
    if access_token is None:
        return []

    try:
        response = _fetch_threshold_list(access_token, target_date)
    except httpx.RequestError:
        logger.exception("Failed to fetch FINRA Reg SHO Threshold List")
        return []

    if response.status_code != 200:
        logger.warning(
            "FINRA Reg SHO Threshold List request failed: HTTP %d", response.status_code
        )
        return []

    try:
        rows = response.json()
    except ValueError:
        logger.exception("FINRA Reg SHO Threshold List returned unparseable JSON")
        return []

    if not isinstance(rows, list) or not rows:
        return []

    candidates = _extract_candidates(rows, target_date)
    if not candidates:
        return []

    candidate_ids = [c.source_document_id for c in candidates]
    existing_ids_result = await db.execute(
        select(Filing.source_document_id).where(
            Filing.source == FilingSource.FINRA,
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
