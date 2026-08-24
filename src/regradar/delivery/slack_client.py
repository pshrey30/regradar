"""Sends filing alerts to Slack via an Incoming Webhook, Block Kit formatted."""

import logging

import httpx

from regradar.delivery.types import DeliveryResult
from regradar.models.enums import DeliveryStatus, RiskLevel

logger = logging.getLogger(__name__)

_RISK_COLOR: dict[RiskLevel, str] = {
    RiskLevel.LOW: "#16A34A",
    RiskLevel.MEDIUM: "#D97706",
    RiskLevel.HIGH: "#EA580C",
    RiskLevel.CRITICAL: "#DC2626",
}
_DEFAULT_COLOR = "#64748B"


def _escape_mrkdwn(text: str) -> str:
    """Escape Slack mrkdwn special characters per Slack's own escaping guidance.

    Order matters: & must be escaped first, or the &amp;/&lt;/&gt; entities
    produced by the later replacements would themselves get re-escaped.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def send_slack_alert(
    webhook_url: str,
    entity_name: str,
    filing_type: str,
    filing_url: str,
    risk_level: RiskLevel | None,
    cco_summary: str,
) -> DeliveryResult:
    color = _RISK_COLOR.get(risk_level, _DEFAULT_COLOR) if risk_level else _DEFAULT_COLOR
    risk_text = risk_level.value if risk_level else "unknown"
    safe_entity_name = _escape_mrkdwn(entity_name)
    safe_cco_summary = _escape_mrkdwn(cco_summary)
    safe_filing_url = _escape_mrkdwn(filing_url)
    payload = {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*{safe_entity_name}* — {filing_type}\n"
                                f"Risk: *{risk_text}*\n"
                                f"{safe_cco_summary}\n"
                                f"<{safe_filing_url}|View filing>"
                            ),
                        },
                    }
                ],
            }
        ]
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(webhook_url, json=payload)
    except httpx.RequestError as exc:
        logger.warning("Slack delivery failed (request error): %s", exc)
        return DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)

    if response.status_code == 200 and response.text.strip() == "ok":
        return DeliveryResult(status=DeliveryStatus.SENT, response_code=response.status_code)
    logger.warning("Slack delivery failed: status=%s body=%r", response.status_code, response.text)
    return DeliveryResult(status=DeliveryStatus.FAILED, response_code=response.status_code)
