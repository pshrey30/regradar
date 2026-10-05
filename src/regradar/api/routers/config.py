"""GET/POST /v1/config/sources — source/domain monitoring config.

GET is readable by any authenticated role (matches source_configs' RLS
SELECT policy); POST is Admin-only. Changes take effect on the next
scheduled Prefect cycle with no redeploy: `ingestion/flows.py`'s
`poll_all_sources` queries `source_configs` fresh each run, so flipping
`is_active` here is all a running scheduler needs to see.
"""

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.deps import AuthenticatedKey
from regradar.api.errors import ApiError
from regradar.api.middleware.rate_limit import enforce_rate_limit, get_authenticated_db
from regradar.models.enums import ApiKeyRole, FilingSource, FilingStatus
from regradar.models.filing import Filing
from regradar.models.source_config import SourceConfig
from regradar.schemas.config import (
    FetchNowRequest,
    FetchNowResponse,
    FetchNowResult,
    PollSourceResponse,
    SourceConfigResponse,
    SourceConfigUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def _pending_filing_ids_for_org(db: AsyncSession, organization_id: uuid.UUID) -> list[uuid.UUID]:
    """Every filing still at status=ingested for this org right now — the
    same "everything pending" semantics as the CLI's process-pending, just
    scoped to one org (process-pending itself has no org filter, which is
    fine for a single-operator CLI but wrong to reuse verbatim from a
    request scoped to one authenticated org)."""
    result = await db.execute(
        select(Filing.id).where(
            Filing.organization_id == organization_id, Filing.status == FilingStatus.INGESTED
        )
    )
    return list(result.scalars().all())


async def _process_filings_in_background(filing_ids: list[uuid.UUID]) -> None:
    """Runs after the HTTP response is sent (FastAPI BackgroundTasks) — each
    filing's own status/ws stream is what makes this visible live on the
    frontend (see workers/pipeline_tasks.py's per-stage status commits).
    One filing's failure never stops the rest — mirrors
    process_pending_filings' own per-filing error isolation.
    """
    from regradar.workers.pipeline_tasks import _mark_filing_failed, _run_pipeline_for_filing

    for filing_id in filing_ids:
        try:
            await _run_pipeline_for_filing(str(filing_id))
        except Exception as exc:  # noqa: BLE001 — one filing's failure must not stop the rest
            logger.exception("fetch-now: processing failed for filing %s", filing_id)
            await _mark_filing_failed(str(filing_id), str(exc))


@router.get("/v1/config/sources", response_model=list[SourceConfigResponse])
async def get_source_config(
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> list[SourceConfigResponse]:
    rows = {row.source: row for row in (await db.execute(select(SourceConfig))).scalars().all()}
    return [
        SourceConfigResponse(
            source=source,
            domains=rows[source].domains if source in rows else [],
            is_active=rows[source].is_active if source in rows else False,
            poll_interval_seconds=rows[source].poll_interval_seconds if source in rows else 300,
            last_polled_at=rows[source].last_polled_at if source in rows else None,
            feed_url=rows[source].feed_url if source in rows else None,
        )
        for source in sorted(FilingSource, key=lambda s: s.value)
    ]


@router.post("/v1/config/sources", response_model=list[SourceConfigResponse])
async def update_source_config(
    body: SourceConfigUpdateRequest,
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> list[SourceConfigResponse]:
    if key.role != ApiKeyRole.ADMIN:
        raise ApiError(
            status_code=403,
            code="forbidden",
            message="Only the Admin role can update source configuration.",
        )

    valid_values = {source.value for source in FilingSource}
    invalid = sorted(set(body.sources) - valid_values)
    if invalid:
        raise ApiError(
            status_code=422,
            code="invalid_sources",
            message=f"Unsupported source(s): {', '.join(invalid)}. Valid values: {', '.join(sorted(valid_values))}.",
        )

    requested = {FilingSource(value) for value in body.sources}

    rows = {row.source: row for row in (await db.execute(select(SourceConfig))).scalars().all()}
    for source in FilingSource:
        row = rows.get(source)
        if source in requested:
            if row is None:
                # SQLAlchemy's column defaults (poll_interval_seconds, id) only
                # apply at flush, not at construction — set explicitly so the
                # response built right after this reflects real values.
                row = SourceConfig(
                    organization_id=key.organization_id,
                    source=source,
                    domains=body.domains,
                    is_active=True,
                    poll_interval_seconds=300,
                    feed_url=body.fda_feed_url if source == FilingSource.FDA else None,
                )
                db.add(row)
                rows[source] = row
            else:
                row.is_active = True
                row.domains = body.domains
                if source == FilingSource.FDA:
                    row.feed_url = body.fda_feed_url
        elif row is not None:
            row.is_active = False

    await db.commit()

    return [
        SourceConfigResponse(
            source=source,
            domains=row.domains,
            is_active=row.is_active,
            poll_interval_seconds=row.poll_interval_seconds,
            last_polled_at=row.last_polled_at,
            feed_url=row.feed_url,
        )
        for source, row in sorted(rows.items(), key=lambda item: item[0].value)
    ]


@router.post("/v1/config/sources/{source}/poll", response_model=PollSourceResponse)
async def poll_source_now(
    source: FilingSource,
    background_tasks: BackgroundTasks,
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> PollSourceResponse:
    """Admin-only: run this one source's ingestion connector right now,
    synchronously, in this request — the on-demand equivalent of waiting for
    poll-all-sources's own cycle (ingestion/flows.py), scoped to a single
    source. Mirrors process_pending_filing's same on-demand,
    no-scheduler-required shape (see filings.py).

    Every filing now pending for this org (not just ones this call itself
    fetched) is then handed to the pipeline in the background — the
    response returns immediately with their ids so the frontend can open a
    status/ws connection per filing and show each one's progress live,
    rather than block this request until every filing finishes.
    """
    if key.role != ApiKeyRole.ADMIN:
        raise ApiError(
            status_code=403,
            code="forbidden",
            message="Only the Admin role can trigger polling.",
        )

    row = (await db.execute(select(SourceConfig).where(SourceConfig.source == source))).scalar_one_or_none()
    if row is None:
        raise ApiError(
            status_code=404,
            code="source_not_configured",
            message=f"{source.value} has not been configured yet.",
        )
    if source == FilingSource.FDA and not row.feed_url:
        raise ApiError(
            status_code=409,
            code="feed_url_missing",
            message="FDA has no feed URL configured yet — save one first.",
        )

    from regradar.ingestion.flows import poll_source as run_poll_source

    new_filings = await run_poll_source(row.id, source)

    # poll_source (ingestion/flows.py) runs the actual DB write in its own,
    # separate session — this request's `db` session never committed, so
    # its RLS role is untouched; refresh just needs a plain re-SELECT to
    # pick up that other session's now-committed last_polled_at.
    await db.refresh(row)

    filing_ids = await _pending_filing_ids_for_org(db, key.organization_id)
    background_tasks.add_task(_process_filings_in_background, filing_ids)

    return PollSourceResponse(
        source=source,
        new_filing_count=len(new_filings),
        last_polled_at=row.last_polled_at,
        processing_filing_ids=filing_ids,
    )


@router.post("/v1/config/sources/fetch-now", response_model=FetchNowResponse)
async def fetch_now(
    body: FetchNowRequest,
    background_tasks: BackgroundTasks,
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> FetchNowResponse:
    """Admin-only: the Regulators panel's "Fetch now" — polls every
    requested (checked) source in one call, then hands every filing now
    pending for this org to the pipeline in the background, same as
    poll_source_now above. FDA has its own single-source button
    (poll_source_now) since it also needs the feed-url gate; this endpoint
    is for SEC/FINRA, which don't.
    """
    if key.role != ApiKeyRole.ADMIN:
        raise ApiError(
            status_code=403,
            code="forbidden",
            message="Only the Admin role can trigger fetching.",
        )

    valid_values = {source.value for source in FilingSource}
    invalid = sorted(set(body.sources) - valid_values)
    if invalid:
        raise ApiError(
            status_code=422,
            code="invalid_sources",
            message=f"Unsupported source(s): {', '.join(invalid)}. Valid values: {', '.join(sorted(valid_values))}.",
        )

    from regradar.ingestion.flows import poll_source as run_poll_source

    results: list[FetchNowResult] = []
    for value in body.sources:
        source = FilingSource(value)
        row = (await db.execute(select(SourceConfig).where(SourceConfig.source == source))).scalar_one_or_none()
        if row is None:
            raise ApiError(
                status_code=404,
                code="source_not_configured",
                message=f"{source.value} has not been configured yet.",
            )
        new_filings = await run_poll_source(row.id, source)
        results.append(FetchNowResult(source=source, new_filing_count=len(new_filings)))

    filing_ids = await _pending_filing_ids_for_org(db, key.organization_id)
    background_tasks.add_task(_process_filings_in_background, filing_ids)

    return FetchNowResponse(results=results, processing_filing_ids=filing_ids)
