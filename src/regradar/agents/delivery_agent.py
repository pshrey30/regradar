"""The real deliver graph node — fans out to Slack, email, and every
registered, filter-matching webhook for a filing, writing a Delivery row
per attempt.

Unlike every other node in this graph, deliver_node both reads AND writes
the database mid-graph: idempotency (never re-send an already-sent
channel) and the webhook fan-out list both require a DB read before
deciding what to send, and multiple Delivery rows per filing (one per
channel/webhook) don't fit the single-row post-graph persistence pattern
used for Extraction/Brief in workers/pipeline_tasks.py. This extends
retrieve_node's existing precedent of an async node with a DB session
threaded through config={"configurable": {"db": db}} from reads to writes.

Two things this node deliberately does NOT do, and why:
- It never reads filing.domain/filing.risk_level from the DB-fetched
  Filing row — pipeline_tasks.py only sets those on the Filing object
  AFTER ainvoke() (and this node) returns, so they're stale mid-run.
  state.domain/state.risk_level (set earlier in this same graph run by
  triage_node) are the only correct source for classification-derived
  content. The Filing row is fetched only for entity_name/filing_type/
  filing_url, which PipelineState doesn't carry.
- It never sets filing.status. pipeline_tasks.py's post-graph code
  unconditionally re-decides filing.status from result["extraction"]/
  result["briefs"] after the graph completes, on the SAME ORM object
  (SQLAlchemy's identity map — db.get(Filing, id) on the same session
  returns the same Python instance) — anything this node set there would
  simply be overwritten moments later. pipeline_tasks.py alone decides
  filing.status; this node signals "delivery ran" via
  state.delivery_status being non-None, which pipeline_tasks.py reads.

DELIV-01: the Slack webhook URL is read per-organization from
`organization_delivery_settings` (SEC-05's `organizations` table plus
this ticket's own settings table), not from a single global env var —
AGENT-10's original single-destination design predated SEC-05's
`organizations` table entirely. Email still reads from
settings.delivery_email_recipient (a single global destination) —
DELIV-01's own acceptance criteria only require per-org storage for
Slack; DELIV-02 (SendGrid) is a separate, not-yet-built ticket. Webhook
fan-out is "every active Webhook row whose filter_domain/filter_min_risk
matches this filing" — webhooks are scoped to api_key_id, not to any
tenant.

DELIV-04: a Critical/High filing whose Slack attempt fails, or whose
organization has no Slack webhook configured at all, automatically gets
an email fallback attempt via settings.admin_fallback_email — but only
when the normal email channel isn't already configured (sendgrid_api_key
+ delivery_email_recipient). If it is, that channel already tries
independently of Slack's outcome, so a redundant fallback send to the
same global recipient would just be a duplicate, not a real gap being
closed. The fallback is recorded as its own Delivery row with
is_fallback=True. Separately, a Redis-backed per-organization counter
tracks *consecutive real Slack failures* (not "never configured" — a
"reconnect Slack" nudge only makes sense for an integration that was
working and started failing) and logs a distinct reconnection-needed
event after 3 in a row, resetting to 0 on the next success.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy import select

from regradar.agents.state import PipelineState
from regradar.core.config import get_settings
from regradar.core.db import set_rls_context
from regradar.core.redis_client import get_redis_client
from regradar.delivery.sendgrid_client import send_email_alert
from regradar.delivery.slack_client import send_slack_alert
from regradar.delivery.types import DeliveryResult
from regradar.delivery.webhook_dispatcher import WebhookValidationError, send_webhook_alert
from regradar.models.delivery import Delivery
from regradar.models.enums import DeliveryChannel, DeliveryStatus, FilingDomain, RiskLevel
from regradar.models.filing import Filing
from regradar.models.organization_delivery_settings import OrganizationDeliverySettings
from regradar.models.webhook import Webhook

logger = logging.getLogger(__name__)

_RISK_ORDER: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

_FALLBACK_RISK_LEVELS = (RiskLevel.HIGH, RiskLevel.CRITICAL)
_SLACK_RECONNECTION_THRESHOLD = 3
_SLACK_FAILURE_STREAK_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days — hygiene, not a real expectation


def _slack_failure_streak_key(organization_id: Any) -> str:
    return f"slack_failure_streak:{organization_id}"


async def _record_slack_failure_and_maybe_notify(organization_id: Any) -> None:
    client = get_redis_client()
    key = _slack_failure_streak_key(organization_id)
    streak = await client.incr(key)
    await client.expire(key, _SLACK_FAILURE_STREAK_TTL_SECONDS)
    if streak >= _SLACK_RECONNECTION_THRESHOLD:
        # Stub notification hook for V1, matching EVAL-06's own
        # cost-alert stub precedent — a future ticket wires this to a
        # real admin-facing channel (email/Slack-to-the-team/etc).
        logger.error(
            "Slack reconnection needed for organization %s — %d consecutive failed deliveries",
            organization_id,
            streak,
        )


async def _reset_slack_failure_streak(organization_id: Any) -> None:
    client = get_redis_client()
    await client.delete(_slack_failure_streak_key(organization_id))


def _webhook_matches(
    webhook: Webhook, domain: FilingDomain | None, risk_level: RiskLevel | None
) -> bool:
    domain_matches = not (
        webhook.filter_domain and domain and webhook.filter_domain != domain.value
    )
    risk_matches = not (
        webhook.filter_min_risk
        and risk_level
        and _RISK_ORDER[risk_level] < _RISK_ORDER[webhook.filter_min_risk]
    )
    return domain_matches and risk_matches


async def _record_delivery(
    db: Any,
    filing_id: Any,
    organization_id: Any,
    channel: DeliveryChannel,
    recipient: str,
    result: DeliveryResult,
    webhook_id: Any = None,
    *,
    is_fallback: bool = False,
) -> None:
    # Real bug found via DELIV-01 live verification: set_rls_context's
    # set_config(..., true) is transaction-scoped and this function commits
    # after every channel — on a pooled connection that's ever touched the
    # GUC before, it reverts to '' (not NULL) once that transaction ends
    # (the same real Postgres behavior SEC-01 already documented), so the
    # *second* channel's insert in one deliver_node run was silently denied
    # by RLS. Re-asserting the context before every write is cheap (two
    # tiny set_config calls) and keeps each channel's Delivery row durable
    # immediately, which is why this commits per-channel instead of once
    # at the end of deliver_node.
    await set_rls_context(db, role="service")
    db.add(
        Delivery(
            organization_id=organization_id,
            filing_id=filing_id,
            channel=channel,
            webhook_id=webhook_id,
            recipient=recipient,
            status=result.status,
            response_code=result.response_code,
            attempt_count=1,
            is_fallback=is_fallback,
            sent_at=datetime.now(UTC) if result.status == DeliveryStatus.SENT else None,
        )
    )
    await db.commit()


async def deliver_node(state: PipelineState, config: RunnableConfig) -> PipelineState:
    if state.briefs is None:
        logger.warning("No briefs available for filing %s; skipping delivery", state.filing_id)
        return state

    db = config["configurable"]["db"]
    settings = get_settings()

    filing = await db.get(Filing, state.filing_id)
    if filing is None:
        logger.warning("Filing %s not found; skipping delivery", state.filing_id)
        return state

    delivery_settings = await db.get(OrganizationDeliverySettings, filing.organization_id)

    existing = await db.execute(
        select(Delivery).where(
            Delivery.filing_id == state.filing_id, Delivery.status == DeliveryStatus.SENT
        )
    )
    already_sent = {(d.channel, d.webhook_id) for d in existing.scalars().all()}

    statuses: list[str] = []
    any_sent = False

    # --- Slack ---
    slack_attempted = False
    slack_failed = False
    if (DeliveryChannel.SLACK, None) not in already_sent:
        slack_url = delivery_settings.slack_webhook_url if delivery_settings else None
        if slack_url:
            slack_attempted = True
            try:
                result = await send_slack_alert(
                    webhook_url=slack_url,
                    entity_name=filing.entity_name,
                    filing_type=filing.filing_type,
                    filing_url=filing.filing_url,
                    risk_level=state.risk_level,
                    cco_summary=state.briefs.cco_summary,
                )
            except Exception as exc:  # noqa: BLE001 — one channel's crash must not block the others,
                # and every attempt still gets a Delivery row per this ticket's acceptance criteria
                logger.warning("Slack delivery raised for filing %s: %s", state.filing_id, exc)
                result = DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)
            await _record_delivery(
                db, filing.id, filing.organization_id, DeliveryChannel.SLACK, "slack:default", result
            )
            if result.status == DeliveryStatus.SENT:
                any_sent = True
            else:
                slack_failed = True
            statuses.append(f"slack={result.status.value}")
        else:
            statuses.append("slack=not_configured")

    if slack_attempted:
        if slack_failed:
            await _record_slack_failure_and_maybe_notify(filing.organization_id)
        else:
            await _reset_slack_failure_streak(filing.organization_id)

    # --- Slack failure fallback (DELIV-04) ---
    # Only when the normal email channel isn't already configured — if it
    # is, it already attempts independently of Slack's outcome below, so a
    # second send to the same global recipient would just be a duplicate.
    primary_email_configured = bool(settings.sendgrid_api_key and settings.delivery_email_recipient)
    if (
        state.risk_level in _FALLBACK_RISK_LEVELS
        and (slack_failed or not slack_attempted)
        and not primary_email_configured
        and (DeliveryChannel.EMAIL, None) not in already_sent
    ):
        if settings.admin_fallback_email:
            try:
                result = await send_email_alert(
                    recipient=settings.admin_fallback_email,
                    entity_name=filing.entity_name,
                    filing_type=filing.filing_type,
                    risk_level=state.risk_level,
                    executive_brief=state.briefs.executive_brief,
                )
            except Exception as exc:  # noqa: BLE001 — see Slack's comment above
                logger.warning("Fallback email delivery raised for filing %s: %s", state.filing_id, exc)
                result = DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)
            await _record_delivery(
                db,
                filing.id,
                filing.organization_id,
                DeliveryChannel.EMAIL,
                settings.admin_fallback_email,
                result,
                is_fallback=True,
            )
            if result.status == DeliveryStatus.SENT:
                any_sent = True
            statuses.append(f"email_fallback={result.status.value}")
        else:
            statuses.append("email_fallback=not_configured")

    # --- Email ---
    if (DeliveryChannel.EMAIL, None) not in already_sent:
        if settings.sendgrid_api_key and settings.delivery_email_recipient:
            recipient = settings.delivery_email_recipient
            try:
                result = await send_email_alert(
                    recipient=recipient,
                    entity_name=filing.entity_name,
                    filing_type=filing.filing_type,
                    risk_level=state.risk_level,
                    executive_brief=state.briefs.executive_brief,
                )
            except Exception as exc:  # noqa: BLE001 — see Slack's comment above
                logger.warning("Email delivery raised for filing %s: %s", state.filing_id, exc)
                result = DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)
            await _record_delivery(
                db, filing.id, filing.organization_id, DeliveryChannel.EMAIL, recipient, result
            )
            if result.status == DeliveryStatus.SENT:
                any_sent = True
            statuses.append(f"email={result.status.value}")
        else:
            statuses.append("email=not_configured")

    # --- Webhooks ---
    # SEC-05: `service` bypasses RLS org-scoping entirely (it needs
    # cross-org write access), so this read must filter to the filing's
    # own organization explicitly — without this, a filing would fan out
    # to every organization's webhooks, not just its own.
    webhooks_result = await db.execute(
        select(Webhook).where(
            Webhook.is_active.is_(True), Webhook.organization_id == filing.organization_id
        )
    )
    for webhook in webhooks_result.scalars().all():
        if (DeliveryChannel.WEBHOOK, webhook.id) in already_sent:
            continue
        if not _webhook_matches(webhook, state.domain, state.risk_level):
            continue
        payload = {
            "filing_id": str(filing.id),
            "entity_name": filing.entity_name,
            "filing_type": filing.filing_type,
            "domain": state.domain.value if state.domain else None,
            "risk_level": state.risk_level.value if state.risk_level else None,
            "executive_brief": state.briefs.executive_brief,
            "filing_url": filing.filing_url,
        }
        try:
            result = await send_webhook_alert(webhook.url, webhook.hmac_secret, payload)
        except WebhookValidationError as exc:
            logger.warning(
                "Webhook %s failed URL validation for filing %s: %s",
                webhook.id,
                state.filing_id,
                exc,
            )
            result = DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)
        except Exception as exc:  # noqa: BLE001 — see Slack's comment above
            logger.warning("Webhook delivery raised for filing %s: %s", state.filing_id, exc)
            result = DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)
        await _record_delivery(
            db,
            filing.id,
            filing.organization_id,
            DeliveryChannel.WEBHOOK,
            webhook.url,
            result,
            webhook_id=webhook.id,
        )
        if result.status == DeliveryStatus.SENT:
            any_sent = True
        statuses.append(f"webhook:{webhook.id}={result.status.value}")

    return state.model_copy(
        update={
            "delivery_status": ", ".join(statuses) if statuses else "none",
            "delivery_success": any_sent,
        }
    )
