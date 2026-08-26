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
silently assumed away.

SEC-03 — replay protection: every signed request also carries an
`X-RegRadar-Timestamp` header, and the timestamp is signed *alongside* the
body (never appended unsigned), following the same pattern Stripe uses for
webhook signing. `verify_webhook_signature()` is the reference
implementation a receiving server should use — see its docstring for the
exact algorithm, and `docs/api-contract.md` for the customer-facing
write-up.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
from urllib.parse import urlparse

import httpx

from regradar.core.config import get_settings
from regradar.delivery.types import DeliveryResult
from regradar.models.enums import DeliveryStatus

logger = logging.getLogger(__name__)

DEFAULT_REPLAY_TOLERANCE_SECONDS = 300


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


def _signed_payload(timestamp: int, body: bytes) -> bytes:
    """timestamp + body, concatenated the way Stripe signs its own webhooks —
    the timestamp is part of what gets signed, never appended unsigned, so a
    captured request's signature can't be replayed with a forged timestamp."""
    return f"{timestamp}.".encode() + body


def _sign_payload(secret: str, timestamp: int, body: bytes, *, algorithm: str) -> str:
    digestmod = getattr(hashlib, algorithm)
    return hmac.new(secret.encode(), _signed_payload(timestamp, body), digestmod).hexdigest()


def verify_webhook_signature(
    secret: str,
    timestamp: str | int,
    body: bytes,
    signature: str,
    *,
    algorithm: str = "sha256",
    tolerance_seconds: int = DEFAULT_REPLAY_TOLERANCE_SECONDS,
) -> bool:
    """Reference verification a receiving server should run on every inbound
    RegRadar webhook request, using its own `X-RegRadar-Timestamp` and
    `X-RegRadar-Signature` header values plus the raw request body:

        verify_webhook_signature(
            secret=your_stored_hmac_secret,
            timestamp=request.headers["X-RegRadar-Timestamp"],
            body=request.raw_body,  # bytes, exactly as received — do not re-serialize
            signature=request.headers["X-RegRadar-Signature"].split("=", 1)[1],
        )

    Returns False (never raises) for: a malformed timestamp, a timestamp
    more than `tolerance_seconds` from the verifier's current time in
    either direction (stale — a replayed old request; or in the future —
    a clock-skew/forgery smell), or a signature that doesn't match. Uses
    `hmac.compare_digest` for the signature comparison so it isn't
    vulnerable to a timing side-channel.
    """
    try:
        timestamp_int = int(timestamp)
    except (TypeError, ValueError):
        return False

    if abs(time.time() - timestamp_int) > tolerance_seconds:
        return False

    expected_signature = _sign_payload(secret, timestamp_int, body, algorithm=algorithm)
    return hmac.compare_digest(expected_signature, signature)


async def send_webhook_alert(url: str, hmac_secret: str, payload: dict) -> DeliveryResult:
    settings = get_settings()
    validate_webhook_url(url)

    body = json.dumps(payload).encode()
    timestamp = int(time.time())
    signature = _sign_payload(hmac_secret, timestamp, body, algorithm=settings.webhook_hmac_algorithm)
    headers = {
        "Content-Type": "application/json",
        "X-RegRadar-Timestamp": str(timestamp),
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
