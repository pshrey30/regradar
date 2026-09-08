"""DELIV-03 — weekly digest job: one email per organization compiling its
trailing-7-day Critical/High filings, scheduled via Celery Beat.

`recipient` is the same global `settings.delivery_email_recipient` every
other email alert already uses (AGENT-10) — this project has no per-org
digest destination and only one real organization exists in practice
(SEC-05's "resume project, not organization-level project" directive).
DELIV-01 added a per-organization Slack webhook table specifically
because that ticket's own acceptance criteria demanded it explicitly;
this ticket's acceptance criteria only ask for the *query* to be grouped
per organization (batching, not per-org destination config), so reusing
the existing global recipient setting is a deliberate, matching scope
decision, not an oversight.

Digest sends are not recorded as `deliveries` rows: that table's
`filing_id` column is NOT NULL (one row per filing-delivery attempt), and
a digest spans many filings, so there is no single filing_id a digest
send could correctly attach to. Idempotency isn't a concern here the way
it is for AGENT-10's per-filing sends — this task only ever runs once
per scheduled week via Celery Beat, not on every filing's pipeline run.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from celery.utils.log import get_task_logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from regradar.core.config import get_settings
from regradar.core.db import get_session_factory, set_rls_context
from regradar.delivery.sendgrid_client import DigestFilingEntry, send_digest_email
from regradar.models.enums import RiskLevel
from regradar.models.filing import Filing
from regradar.models.organization import Organization
from regradar.workers.celery_app import celery_app

logger = get_task_logger(__name__)

_DIGEST_RISK_LEVELS = (RiskLevel.CRITICAL, RiskLevel.HIGH)
_DIGEST_WINDOW = timedelta(days=7)


async def _digest_entries_for_organization(
    organization_id, since: datetime
) -> list[DigestFilingEntry]:
    session_factory = get_session_factory()
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        result = await db.execute(
            select(Filing)
            .where(
                Filing.organization_id == organization_id,
                Filing.risk_level.in_(_DIGEST_RISK_LEVELS),
                Filing.published_at >= since,
            )
            .options(selectinload(Filing.brief))
            .order_by(Filing.published_at.desc())
        )
        filings = result.scalars().all()

    entries = []
    for filing in filings:
        if filing.brief is None:
            # A filing can be classified Critical/High without a brief yet
            # existing (still mid-pipeline, or summarization failed) —
            # skip it from the digest rather than crash on missing text;
            # it'll appear once its brief lands, still within the same
            # trailing-7-day window on the next scheduled run.
            logger.warning(
                "Filing %s is %s risk but has no brief yet; omitting from digest",
                filing.id,
                filing.risk_level.value if filing.risk_level else "unknown",
            )
            continue
        # The query's Filing.risk_level.in_(_DIGEST_RISK_LEVELS) filter
        # already guarantees this is non-null — narrows the ORM column's
        # RiskLevel | None type for mypy.
        assert filing.risk_level is not None
        entries.append(
            DigestFilingEntry(
                entity_name=filing.entity_name,
                risk_level=filing.risk_level,
                executive_brief=filing.brief.executive_brief,
            )
        )
    return entries


async def _send_all_digests() -> None:
    settings = get_settings()
    if not settings.delivery_email_recipient:
        logger.warning("delivery_email_recipient not configured; skipping weekly digest entirely")
        return

    session_factory = get_session_factory()
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        organizations = (await db.execute(select(Organization))).scalars().all()

    since = datetime.now(UTC) - _DIGEST_WINDOW
    for org in organizations:
        entries = await _digest_entries_for_organization(org.id, since)
        result = await send_digest_email(
            recipient=settings.delivery_email_recipient,
            organization_name=org.name,
            filings=entries,
        )
        logger.info(
            "Weekly digest for organization %s (%s): %d filing(s), status=%s",
            org.id,
            org.name,
            len(entries),
            result.status.value,
        )


@celery_app.task(name="regradar.workers.digest_tasks.send_weekly_digest")
def send_weekly_digest() -> None:
    asyncio.run(_send_all_digests())
