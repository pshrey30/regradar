"""Signs and dispatches filing-alert payloads to customer-registered webhooks.

Minimal SSRF-prevention validator only — checks the literal function this
ticket's own spec calls for (reject private/loopback/link-local/reserved
resolved addresses, require https outside development). Full
registration-time validation, DNS-rebinding re-checks on every retry, and
replay protection (a timestamp signed alongside the body, per SEC-03) are
separate tickets (SEC-02, SEC-03, API-08) not yet built — this only covers
the dispatch-time half AGENT-10 needs.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
from urllib.parse import urlparse

import httpx

from regradar.core.config import get_settings
from regradar.delivery.types import DeliveryResult
from regradar.models.enums import DeliveryStatus

logger = logging.getLogger(__name__)


class WebhookValidationError(ValueError):
    """Raised when a webhook URL fails SSRF validation."""


def validate_webhook_url(url: str) -> None:
    settings = get_settings()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebhookValidationError(f"Unsupported webhook URL scheme: {url}")
    if settings.env != "development" and parsed.scheme != "https":
        raise WebhookValidationError(f"Webhook URL must use https in {settings.env}: {url}")
    if not parsed.hostname:
        raise WebhookValidationError(f"Webhook URL has no hostname: {url}")

    try:
        resolved = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise WebhookValidationError(
            f"Could not resolve webhook host {parsed.hostname}: {exc}"
        ) from exc

    for _family, _type, _proto, _canonname, sockaddr in resolved:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise WebhookValidationError(f"Webhook URL resolves to a non-public address ({ip}): {url}")


def _sign_payload(secret: str, body: bytes) -> str:
    settings = get_settings()
    digestmod = getattr(hashlib, settings.webhook_hmac_algorithm)
    return hmac.new(secret.encode(), body, digestmod).hexdigest()


async def send_webhook_alert(url: str, hmac_secret: str, payload: dict) -> DeliveryResult:
    settings = get_settings()
    validate_webhook_url(url)

    body = json.dumps(payload).encode()
    signature = _sign_payload(hmac_secret, body)
    headers = {
        "Content-Type": "application/json",
        "X-RegRadar-Signature": f"{settings.webhook_hmac_algorithm}={signature}",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, content=body, headers=headers)
    except httpx.RequestError as exc:
        logger.warning("Webhook delivery failed (request error) for %s: %s", url, exc)
        return DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)

    if 200 <= response.status_code < 300:
        return DeliveryResult(status=DeliveryStatus.SENT, response_code=response.status_code)
    logger.warning("Webhook delivery failed for %s: status=%s", url, response.status_code)
    return DeliveryResult(status=DeliveryStatus.FAILED, response_code=response.status_code)
