"""Unit tests for webhook URL SSRF validation and signed dispatch — no real network calls."""

import hashlib
import hmac
import json
import os
import time

os.environ.setdefault("APP_SECRET_KEY", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_BUCKET_NAME", "test-bucket")
os.environ.setdefault("S3_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("HUGGINGFACE_API_TOKEN", "test")
os.environ.setdefault("SEC_EDGAR_USER_AGENT", "RegRadar/1.0 (test@example.com)")

from unittest.mock import (  # noqa: I001
    AsyncMock,
    MagicMock,
    patch,
)

import pytest

# Import models.enums first to break circular dependency via config -> models
from regradar.models.enums import DeliveryStatus
from regradar.delivery.webhook_dispatcher import (
    WebhookValidationError,
    send_webhook_alert,
    validate_webhook_url,
    verify_webhook_signature,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from regradar.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_validate_webhook_url_accepts_public_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
        validate_webhook_url("https://example.com/webhook")  # should not raise


def test_validate_webhook_url_rejects_private_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 0))]),
        pytest.raises(WebhookValidationError),
    ):
        validate_webhook_url("https://internal.example.com/webhook")


def test_validate_webhook_url_rejects_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 0))]),
        pytest.raises(WebhookValidationError),
    ):
        validate_webhook_url("https://localhost.example.com/webhook")


def test_validate_webhook_url_rejects_link_local_metadata_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV", "production")
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("169.254.169.254", 0))]),
        pytest.raises(WebhookValidationError),
    ):
        validate_webhook_url("https://metadata.example.com/webhook")


def test_validate_webhook_url_rejects_non_https_outside_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV", "production")
    with pytest.raises(WebhookValidationError):
        validate_webhook_url("http://example.com/webhook")


def test_validate_webhook_url_allows_non_https_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV", "development")
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
        validate_webhook_url("http://example.com/webhook")  # should not raise


@pytest.mark.asyncio
async def test_send_webhook_alert_signs_body_with_given_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV", "development")
    payload = {"filing_id": "abc123", "entity_name": "Acme Corp"}
    secret = "webhook-specific-secret"

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await send_webhook_alert("https://example.com/hook", secret, payload)

    assert result.status == DeliveryStatus.SENT
    call_kwargs = mock_client.post.call_args.kwargs
    sent_body = call_kwargs["content"]
    sent_headers = call_kwargs["headers"]
    sent_timestamp = sent_headers["X-RegRadar-Timestamp"]
    signed_payload = f"{sent_timestamp}.".encode() + sent_body
    expected_signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    assert sent_headers["X-RegRadar-Signature"] == f"sha256={expected_signature}"
    assert json.loads(sent_body) == payload


@pytest.mark.asyncio
async def test_send_webhook_alert_uses_different_signature_for_different_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV", "development")
    payload = {"filing_id": "abc123"}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    signatures = []
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await send_webhook_alert("https://example.com/hook", "secret-one", payload)
        signatures.append(mock_client.post.call_args.kwargs["headers"]["X-RegRadar-Signature"])
        await send_webhook_alert("https://example.com/hook", "secret-two", payload)
        signatures.append(mock_client.post.call_args.kwargs["headers"]["X-RegRadar-Signature"])

    assert signatures[0] != signatures[1]


@pytest.mark.asyncio
async def test_send_webhook_alert_returns_failed_on_non_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "development")
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await send_webhook_alert("https://example.com/hook", "secret", {"a": 1})

    assert result.status == DeliveryStatus.FAILED
    assert result.response_code == 500


@pytest.mark.asyncio
async def test_send_webhook_alert_raises_validation_error_for_private_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV", "development")
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 0))]),
        pytest.raises(WebhookValidationError),
    ):
        await send_webhook_alert("https://internal.example.com/hook", "secret", {"a": 1})


@pytest.mark.asyncio
async def test_send_webhook_alert_includes_timestamp_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "development")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await send_webhook_alert("https://example.com/hook", "secret", {"a": 1})

    sent_headers = mock_client.post.call_args.kwargs["headers"]
    assert sent_headers["X-RegRadar-Timestamp"].isdigit()


@pytest.mark.asyncio
async def test_signing_same_payload_at_different_timestamps_produces_different_signatures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-03's own acceptance criterion, verbatim: two identical payloads
    signed at different timestamps must produce different signatures — the
    whole point of binding the timestamp into what's signed."""
    monkeypatch.setenv("ENV", "development")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    payload = {"filing_id": "abc123"}
    signatures = []
    timestamps = [1_700_000_000, 1_700_000_100]
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        for ts in timestamps:
            with patch("time.time", return_value=float(ts)):
                await send_webhook_alert("https://example.com/hook", "secret", payload)
            headers = mock_client.post.call_args.kwargs["headers"]
            assert headers["X-RegRadar-Timestamp"] == str(ts)
            signatures.append(headers["X-RegRadar-Signature"])

    assert signatures[0] != signatures[1]


def test_verify_webhook_signature_accepts_valid_fresh_signature() -> None:
    secret = "my-secret"
    body = json.dumps({"filing_id": "abc123"}).encode()
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.".encode() + body
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(secret, timestamp, body, signature) is True


def test_verify_webhook_signature_rejects_stale_timestamp() -> None:
    secret = "my-secret"
    body = json.dumps({"filing_id": "abc123"}).encode()
    stale_timestamp = int(time.time()) - 600  # 10 minutes old
    signed_payload = f"{stale_timestamp}.".encode() + body
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(secret, stale_timestamp, body, signature) is False


def test_verify_webhook_signature_rejects_future_timestamp() -> None:
    """A timestamp far in the future is as suspicious as a stale one — not
    just clock skew tolerance, a real forgery/replay smell either way."""
    secret = "my-secret"
    body = json.dumps({"filing_id": "abc123"}).encode()
    future_timestamp = int(time.time()) + 600
    signed_payload = f"{future_timestamp}.".encode() + body
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(secret, future_timestamp, body, signature) is False


def test_verify_webhook_signature_rejects_tampered_body() -> None:
    secret = "my-secret"
    original_body = json.dumps({"filing_id": "abc123"}).encode()
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.".encode() + original_body
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()

    tampered_body = json.dumps({"filing_id": "xyz999"}).encode()
    assert verify_webhook_signature(secret, timestamp, tampered_body, signature) is False


def test_verify_webhook_signature_rejects_malformed_timestamp() -> None:
    assert verify_webhook_signature("secret", "not-a-number", b"{}", "deadbeef") is False
