"""POST /v1/config/sources — Admin-only source/domain monitoring config.

Changes take effect on the next scheduled Prefect cycle with no redeploy:
`ingestion/flows.py`'s `poll_all_sources` queries `source_configs` fresh
each run, so flipping `is_active` here is all a running scheduler needs to
see.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.deps import AuthenticatedKey
from regradar.api.errors import ApiError
from regradar.api.middleware.rate_limit import enforce_rate_limit
from regradar.core.db import get_db
from regradar.models.enums import ApiKeyRole, FilingSource
from regradar.models.source_config import SourceConfig
from regradar.schemas.config import SourceConfigResponse, SourceConfigUpdateRequest

router = APIRouter()


@router.post("/v1/config/sources", response_model=list[SourceConfigResponse])
async def update_source_config(
    body: SourceConfigUpdateRequest,
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
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
                row = SourceConfig(source=source, domains=body.domains, is_active=True)
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
