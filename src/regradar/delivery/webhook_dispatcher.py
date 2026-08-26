"""Signs and dispatches filing-alert payloads to customer-registered webhooks.

`validate_webhook_url` closes SEC-02 in full: it rejects any URL resolving
(via real DNS resolution, not string inspection) to a private/loopback/
link-local/reserved address — including cloud metadata endpoints like
169.254.169.254 — and requires https outside `ENV=development`. It runs at
both real call sites SEC-02 requires: registration time (API-08's
`POST /v1/webhooks`, which 422s on failure) and dispatch time (here, on
every `send_webhook_alert` call, so a DNS change between registration and
delivery is still caught). One residual, documented risk SEC-02's own
acceptance criteria doesn't require closing: this validates via its own
`socket.getaddrinfo` call, then `httpx.AsyncClient` performs its own
independent DNS resolution to actually connect — a sub-second-TTL
DNS-rebinding attacker could theoretically change the record between those
two calls. Closing that fully would mean connecting directly to the
validated IP rather than re-resolving the hostname, which is real
additional work beyond what this ticket asks for; noted here rather than
silently assumed away. Replay protection (SEC-03) is a separate, still
unbuilt ticket.
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
