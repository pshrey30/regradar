"""GET /v1/filings — paginated, filterable list of completed filings.

Only filings with status="complete" are returned (they're the only ones
with a real Brief row to source executive_brief from). An Executive-role
caller's effective risk filter is always intersected with {HIGH, CRITICAL}
— silently narrowed, never a 403 — per the Security & Access Document's
permission matrix (the ticket's own AI Coding Prompt only describes the
field-level restriction, which this endpoint's response schema already
satisfies by construction: it never includes extraction data for any
role).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.deps import AuthenticatedKey
from regradar.api.middleware.rate_limit import enforce_rate_limit
from regradar.core.db import get_db
from regradar.models.brief import Brief
from regradar.models.enums import ApiKeyRole, FilingDomain, FilingStatus, RiskLevel
from regradar.models.filing import Filing
from regradar.schemas.filings import FilingListItem, FilingListResponse

router = APIRouter()

_EXECUTIVE_ALLOWED_RISK_LEVELS = {RiskLevel.HIGH, RiskLevel.CRITICAL}


def _build_filters(
    *,
    role: ApiKeyRole,
    domain: FilingDomain | None,
    risk: RiskLevel | None,
    since: datetime | None,
) -> list:
    filters: list = [Filing.status == FilingStatus.COMPLETE]

    if domain is not None:
        filters.append(Filing.domain == domain)

    if role == ApiKeyRole.EXECUTIVE:
        # Intersect the requested risk (or "any") with the Executive-allowed
        # set. An empty intersection produces Filing.risk_level.in_(set())
        # which SQLAlchemy compiles to an always-false clause — an empty
        # result, not an error.
        effective_risk = (
            {risk} & _EXECUTIVE_ALLOWED_RISK_LEVELS
            if risk is not None
            else _EXECUTIVE_ALLOWED_RISK_LEVELS
        )
        filters.append(Filing.risk_level.in_(effective_risk))
    elif risk is not None:
        filters.append(Filing.risk_level == risk)

    if since is not None:
        filters.append(Filing.published_at >= since)

    return filters


@router.get("/v1/filings", response_model=FilingListResponse)
async def list_filings(
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    domain: FilingDomain | None = Query(default=None),
    risk: RiskLevel | None = Query(default=None),
    since: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> FilingListResponse:
    filters = _build_filters(role=key.role, domain=domain, risk=risk, since=since)

    total_stmt = select(func.count()).select_from(Filing).where(and_(*filters))
    total = (await db.execute(total_stmt)).scalar_one()

    page_stmt = (
        select(Filing, Brief.executive_brief)
        .join(Brief, Brief.filing_id == Filing.id)
        .where(and_(*filters))
        .order_by(Filing.published_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = (await db.execute(page_stmt)).all()

    data = [
        FilingListItem(
            id=filing.id,
            entity_name=filing.entity_name,
            filing_type=filing.filing_type,
            domain=filing.domain,
            risk_level=filing.risk_level,
            published_at=filing.published_at,
            executive_brief=executive_brief,
        )
        for filing, executive_brief in rows
    ]

    return FilingListResponse(data=data, page=page, page_size=page_size, total=total)
