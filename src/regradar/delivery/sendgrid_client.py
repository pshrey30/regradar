"""Sends filing alerts as HTML email via SendGrid's mail/send API."""

import html
import logging

import httpx

from regradar.core.config import get_settings
from regradar.delivery.types import DeliveryResult
from regradar.models.enums import DeliveryStatus, RiskLevel

logger = logging.getLogger(__name__)

SENDGRID_MAIL_SEND_URL = "https://api.sendgrid.com/v3/mail/send"


def _render_html(entity_name: str, filing_type: str, risk_level: RiskLevel | None, executive_brief: str) -> str:
    risk_text = risk_level.value if risk_level else "unknown"
    safe_entity_name = html.escape(entity_name)
    safe_filing_type = html.escape(filing_type)
    safe_executive_brief = html.escape(executive_brief)
    return (
        f"<h2>{safe_entity_name} — {safe_filing_type}</h2>"
        f"<p><strong>Risk level:</strong> {risk_text}</p>"
        f"<p>{safe_executive_brief}</p>"
    )


async def send_email_alert(
    recipient: str,
    entity_name: str,
    filing_type: str,
    risk_level: RiskLevel | None,
    executive_brief: str,
) -> DeliveryResult:
    settings = get_settings()
    if not settings.sendgrid_api_key:
        logger.warning("SendGrid not configured; skipping email delivery")
        return DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)

    html_body = _render_html(entity_name, filing_type, risk_level, executive_brief)
    payload = {
        "personalizations": [{"to": [{"email": recipient}]}],
        "from": {"email": settings.sendgrid_from_email},
        "subject": f"RegRadar Alert: {entity_name} — {filing_type}",
        "content": [{"type": "text/html", "value": html_body}],
    }
    headers = {"Authorization": f"Bearer {settings.sendgrid_api_key.get_secret_value()}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(SENDGRID_MAIL_SEND_URL, json=payload, headers=headers)
    except httpx.RequestError as exc:
        logger.warning("SendGrid delivery failed (request error): %s", exc)
        return DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)

    if response.status_code == 202:
        return DeliveryResult(status=DeliveryStatus.SENT, response_code=response.status_code)
    logger.warning("SendGrid delivery failed: status=%s body=%r", response.status_code, response.text)
    return DeliveryResult(status=DeliveryStatus.FAILED, response_code=response.status_code)
