"""Unit tests for the Slack delivery client — no real network calls."""

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
from regradar.delivery.slack_client import send_slack_alert


def _mock_response(status_code: int, text: str) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    return response


@pytest.mark.asyncio
async def test_send_slack_alert_returns_sent_on_200_ok() -> None:
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response(200, "ok")
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await send_slack_alert(
            webhook_url="https://hooks.slack.com/services/T/B/X",
            entity_name="Acme Corp",
            filing_type="10-K",
            filing_url="https://example.com/filing",
            risk_level=RiskLevel.HIGH,
            cco_summary="High risk filing requires attention.",
        )

    assert result.status == DeliveryStatus.SENT
    assert result.response_code == 200
    mock_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_slack_alert_returns_failed_on_non_200() -> None:
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response(400, "invalid_payload")
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await send_slack_alert(
            webhook_url="https://hooks.slack.com/services/T/B/X",
            entity_name="Acme Corp",
            filing_type="10-K",
            filing_url="https://example.com/filing",
            risk_level=RiskLevel.LOW,
            cco_summary="Low risk.",
        )

    assert result.status == DeliveryStatus.FAILED
    assert result.response_code == 400


@pytest.mark.asyncio
async def test_send_slack_alert_returns_failed_on_request_error() -> None:
    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.ConnectTimeout("timed out")
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await send_slack_alert(
            webhook_url="https://hooks.slack.com/services/T/B/X",
            entity_name="Acme Corp",
            filing_type="10-K",
            filing_url="https://example.com/filing",
            risk_level=None,
            cco_summary="Unknown risk.",
        )

    assert result.status == DeliveryStatus.FAILED
    assert result.response_code is None


@pytest.mark.asyncio
async def test_send_slack_alert_includes_entity_name_and_risk_in_payload() -> None:
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response(200, "ok")
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with patch("httpx.AsyncClient", return_value=mock_client):
        await send_slack_alert(
            webhook_url="https://hooks.slack.com/services/T/B/X",
            entity_name="Acme Corp",
            filing_type="10-K",
            filing_url="https://example.com/filing",
            risk_level=RiskLevel.CRITICAL,
            cco_summary="Critical risk.",
        )

    call_kwargs = mock_client.post.call_args.kwargs
    payload_text = str(call_kwargs["json"])
    assert "Acme Corp" in payload_text
    assert "critical" in payload_text
    assert "https://example.com/filing" in payload_text
