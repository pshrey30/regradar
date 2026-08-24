"""Unit tests for the SendGrid delivery client — no real network calls."""

import os

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

import httpx
import pytest

# Import models.enums first to break circular dependency via config -> models
from regradar.models.enums import (
    DeliveryStatus,
    RiskLevel,
)
from regradar.delivery.sendgrid_client import send_email_alert


def _mock_response(status_code: int, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    return response


@pytest.mark.asyncio
async def test_send_email_alert_returns_sent_on_202(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENDGRID_API_KEY", "sg-test-key")
    from regradar.core.config import get_settings

    get_settings.cache_clear()

    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response(202)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await send_email_alert(
            recipient="alerts@example.com",
            entity_name="Acme Corp",
            filing_type="10-K",
            risk_level=RiskLevel.HIGH,
            executive_brief="Filing summary text.",
        )

    assert result.status == DeliveryStatus.SENT
    assert result.response_code == 202
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_send_email_alert_returns_failed_on_non_202(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENDGRID_API_KEY", "sg-test-key")
    from regradar.core.config import get_settings

    get_settings.cache_clear()

    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response(401, "unauthorized")
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await send_email_alert(
            recipient="alerts@example.com",
            entity_name="Acme Corp",
            filing_type="10-K",
            risk_level=RiskLevel.HIGH,
            executive_brief="Filing summary text.",
        )

    assert result.status == DeliveryStatus.FAILED
    assert result.response_code == 401
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_send_email_alert_returns_failed_on_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENDGRID_API_KEY", "sg-test-key")
    from regradar.core.config import get_settings

    get_settings.cache_clear()

    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.ConnectTimeout("timed out")
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await send_email_alert(
            recipient="alerts@example.com",
            entity_name="Acme Corp",
            filing_type="10-K",
            risk_level=None,
            executive_brief="Filing summary text.",
        )

    assert result.status == DeliveryStatus.FAILED
    assert result.response_code is None
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_send_email_alert_returns_failed_when_sendgrid_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    from regradar.core.config import get_settings

    get_settings.cache_clear()

    result = await send_email_alert(
        recipient="alerts@example.com",
        entity_name="Acme Corp",
        filing_type="10-K",
        risk_level=RiskLevel.HIGH,
        executive_brief="Filing summary text.",
    )

    assert result.status == DeliveryStatus.FAILED
    assert result.response_code is None
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_send_email_alert_escapes_html_in_executive_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENDGRID_API_KEY", "sg-test-key")
    from regradar.core.config import get_settings

    get_settings.cache_clear()

    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response(202)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    malicious_brief = "<script>alert(1)</script>"

    with patch("httpx.AsyncClient", return_value=mock_client):
        await send_email_alert(
            recipient="alerts@example.com",
            entity_name="Acme Corp",
            filing_type="10-K",
            risk_level=RiskLevel.HIGH,
            executive_brief=malicious_brief,
        )

    call_kwargs = mock_client.post.call_args.kwargs
    html_value = call_kwargs["json"]["content"][0]["value"]
    assert "<script>" not in html_value
    assert "&lt;script&gt;" in html_value
    get_settings.cache_clear()
