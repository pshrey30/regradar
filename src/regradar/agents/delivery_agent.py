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

No organization concept exists in the schema (SEC-05 is a separate,
deferred ticket) — Slack and email are single global destinations
(settings.slack_webhook_url / settings.delivery_email_recipient), not
per-organization. Webhook fan-out is "every active Webhook row whose
filter_domain/filter_min_risk matches this filing" — webhooks are scoped
to api_key_id, not to any tenant.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy import select

from regradar.agents.state import PipelineState
from regradar.core.config import get_settings
from regradar.delivery.sendgrid_client import send_email_alert
from regradar.delivery.slack_client import send_slack_alert
from regradar.delivery.types import DeliveryResult
from regradar.delivery.webhook_dispatcher import WebhookValidationError, send_webhook_alert
from regradar.models.delivery import Delivery
from regradar.models.enums import DeliveryChannel, DeliveryStatus, FilingDomain, RiskLevel
from regradar.models.filing import Filing
from regradar.models.webhook import Webhook

logger = logging.getLogger(__name__)

_RISK_ORDER: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


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
    channel: DeliveryChannel,
    recipient: str,
    result: DeliveryResult,
    webhook_id: Any = None,
) -> None:
    db.add(
        Delivery(
            filing_id=filing_id,
            channel=channel,
            webhook_id=webhook_id,
            recipient=recipient,
            status=result.status,
            response_code=result.response_code,
            attempt_count=1,
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

    existing = await db.execute(
        select(Delivery).where(
            Delivery.filing_id == state.filing_id, Delivery.status == DeliveryStatus.SENT
        )
    )
    already_sent = {(d.channel, d.webhook_id) for d in existing.scalars().all()}

    statuses: list[str] = []

    # --- Slack ---
    if (DeliveryChannel.SLACK, None) not in already_sent:
        if settings.slack_webhook_url:
            slack_url = settings.slack_webhook_url.get_secret_value()
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
            await _record_delivery(db, filing.id, DeliveryChannel.SLACK, slack_url, result)
            statuses.append(f"slack={result.status.value}")
        else:
            statuses.append("slack=not_configured")

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
            await _record_delivery(db, filing.id, DeliveryChannel.EMAIL, recipient, result)
            statuses.append(f"email={result.status.value}")
        else:
            statuses.append("email=not_configured")

    # --- Webhooks ---
    webhooks_result = await db.execute(select(Webhook).where(Webhook.is_active.is_(True)))
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
            db, filing.id, DeliveryChannel.WEBHOOK, webhook.url, result, webhook_id=webhook.id
        )
        statuses.append(f"webhook:{webhook.id}={result.status.value}")

    return state.model_copy(update={"delivery_status": ", ".join(statuses) if statuses else "none"})
