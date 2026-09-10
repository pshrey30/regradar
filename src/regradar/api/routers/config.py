"""GET/POST /v1/config/sources — source/domain monitoring config.

GET is readable by any authenticated role (matches source_configs' RLS
SELECT policy); POST is Admin-only. Changes take effect on the next
scheduled Prefect cycle with no redeploy: `ingestion/flows.py`'s
`poll_all_sources` queries `source_configs` fresh each run, so flipping
`is_active` here is all a running scheduler needs to see.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.deps import AuthenticatedKey
from regradar.api.errors import ApiError
from regradar.api.middleware.rate_limit import enforce_rate_limit, get_authenticated_db
from regradar.models.enums import ApiKeyRole, FilingSource
from regradar.models.source_config import SourceConfig
from regradar.schemas.config import SourceConfigResponse, SourceConfigUpdateRequest

router = APIRouter()


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
                )
                db.add(row)
                rows[source] = row
            else:
                row.is_active = True
                row.domains = body.domains
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
        )
        for source, row in sorted(rows.items(), key=lambda item: item[0].value)
    ]
