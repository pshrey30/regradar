"""GET /v1/activity — a chronological feed of alerts actually sent, so a
user can see "what went out and when" from the dashboard instead of only
finding out via Slack/email/webhook after the fact.

Reads the `deliveries` table every AGENT-10 delivery attempt already
writes to — no new tracking, just a read view over data that already
exists. Every authenticated role can reach this endpoint (org-scoping is
migration 0021's `deliveries_select_authenticated` RLS policy), but the
role-scoped dashboard feature narrows *which* alerts it actually returns:
a non-Admin/Executive role only ever sees alerts for filings in its own
domain(s) — see core/domain_scope.py. An Eng Lead's activity feed shows
Engineering alerts only, never Financial or Clinical.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from regradar.api.deps import AuthenticatedKey
from regradar.api.middleware.rate_limit import enforce_rate_limit, get_authenticated_db
from regradar.core.domain_scope import allowed_domains_for_role
from regradar.models.delivery import Delivery
from regradar.models.filing import Filing
from regradar.schemas.activity import ActivityItem

router = APIRouter()


@router.get("/v1/activity", response_model=list[ActivityItem])
async def list_activity(
    limit: int = Query(default=30, ge=1, le=100),
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> list[ActivityItem]:
    at = func.coalesce(Delivery.sent_at, Delivery.created_at)
    stmt = (
        select(Delivery, Filing.entity_name, Filing.filing_type, Filing.domain, Filing.risk_level, at)
        .join(Filing, Filing.id == Delivery.filing_id)
        .order_by(at.desc())
        .limit(limit)
    )
    allowed_domains = allowed_domains_for_role(key.role)
    if allowed_domains is not None:
        stmt = stmt.where(Filing.domain.in_(allowed_domains))
    rows = (await db.execute(stmt)).all()

    return [
        ActivityItem(
            id=delivery.id,
            filing_id=delivery.filing_id,
            entity_name=entity_name,
            filing_type=filing_type,
            domain=domain,
            risk_level=risk_level,
            channel=delivery.channel,
            status=delivery.status,
            is_fallback=delivery.is_fallback,
            at=at_value,
            error_message=delivery.error_message,
        )
        for delivery, entity_name, filing_type, domain, risk_level, at_value in rows
    ]
