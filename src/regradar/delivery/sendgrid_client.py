"""Sends filing alerts as HTML email via SendGrid's mail/send API."""

import html
import logging
from dataclasses import dataclass

import httpx

from regradar.core.config import get_settings
from regradar.delivery.types import DeliveryResult
from regradar.models.enums import DeliveryStatus, RiskLevel

logger = logging.getLogger(__name__)

SENDGRID_MAIL_SEND_URL = "https://api.sendgrid.com/v3/mail/send"

# DELIV-03: sorts a digest's filings Critical-first, matching the ticket's
# "sorted by risk level descending" requirement — LOW/MEDIUM never appear
# in a digest (only Critical/High are ever included), so they're absent
# here rather than given an arbitrary rank.
_DIGEST_RISK_SORT_ORDER: dict[RiskLevel, int] = {
    RiskLevel.CRITICAL: 0,
    RiskLevel.HIGH: 1,
}


@dataclass(frozen=True)
class DigestFilingEntry:
    """One filing's row in a weekly digest email (DELIV-03)."""

    entity_name: str
    risk_level: RiskLevel
    executive_brief: str


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


def _render_digest_html(organization_name: str, filings: list[DigestFilingEntry]) -> str:
    safe_org_name = html.escape(organization_name)
    if not filings:
        return (
            f"<h2>Weekly Digest — {safe_org_name}</h2>"
            "<p>No Critical or High risk filings this week.</p>"
        )
    ordered = sorted(filings, key=lambda f: _DIGEST_RISK_SORT_ORDER[f.risk_level])
    rows = "".join(
        f"<li><strong>{html.escape(f.entity_name)}</strong> "
        f"(<em>{html.escape(f.risk_level.value)}</em>): {html.escape(f.executive_brief)}</li>"
        for f in ordered
    )
    return f"<h2>Weekly Digest — {safe_org_name}</h2><ul>{rows}</ul>"


async def _send_html_email(*, recipient: str, subject: str, html_body: str) -> DeliveryResult:
    settings = get_settings()
    if not settings.sendgrid_api_key:
        logger.warning("SendGrid not configured; skipping email delivery")
        return DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)

    payload = {
        "personalizations": [{"to": [{"email": recipient}]}],
        "from": {"email": settings.sendgrid_from_email},
        "reply_to": {"email": settings.sendgrid_reply_to},
        "subject": subject,
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


async def send_email_alert(
    recipient: str,
    entity_name: str,
    filing_type: str,
    risk_level: RiskLevel | None,
    executive_brief: str,
) -> DeliveryResult:
    html_body = _render_html(entity_name, filing_type, risk_level, executive_brief)
    return await _send_html_email(
        recipient=recipient,
        subject=f"RegRadar Alert: {entity_name} — {filing_type}",
        html_body=html_body,
    )


async def send_digest_email(
    recipient: str,
    organization_name: str,
    filings: list[DigestFilingEntry],
) -> DeliveryResult:
    """DELIV-03: one email per organization compiling its trailing-7-day
    Critical/High filings, sorted Critical-first. An empty `filings` list
    still sends a short "nothing to report" digest — see
    _render_digest_html — rather than being skipped by the caller."""
    html_body = _render_digest_html(organization_name, filings)
    return await _send_html_email(
        recipient=recipient,
        subject=f"RegRadar Weekly Digest — {organization_name}",
        html_body=html_body,
    )
