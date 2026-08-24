"""Unit tests for the Delivery Agent's fan-out, idempotency, and filtering logic.

All HTTP clients (Slack/SendGrid/webhook) are mocked at the send_*_alert
function boundary — no real network calls. The DB session is an AsyncMock,
matching the pattern in test_pipeline_tasks.py.
"""

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

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from regradar.agents.delivery_agent import deliver_node
from regradar.agents.state import BriefSet, PipelineState
from regradar.delivery.types import DeliveryResult
from regradar.models.enums import DeliveryChannel, DeliveryStatus, FilingDomain, RiskLevel
from regradar.models.filing import Filing
from regradar.models.webhook import Webhook


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from regradar.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_state(risk_level: RiskLevel = RiskLevel.HIGH) -> PipelineState:
    return PipelineState(
        filing_id=uuid.uuid4(),
        raw_text="",
        domain=FilingDomain.FINANCIAL,
        risk_level=risk_level,
        briefs=BriefSet(
            executive_brief="Filing summary.",
            cco_summary="Board summary.",
            analyst_summary="- obligation one",
            engineer_summary="filing_id=x status=processed",
            model_used="llama3.1",
        ),
    )


def _make_filing(filing_id: uuid.UUID) -> MagicMock:
    filing = MagicMock(spec=Filing)
    filing.id = filing_id
    filing.entity_name = "Acme Corp"
    filing.filing_type = "10-K"
    filing.filing_url = "https://example.com/filing"
    return filing


def _make_db(filing: MagicMock, existing_deliveries: list, webhooks: list) -> AsyncMock:
    db = AsyncMock()
    db.get = AsyncMock(return_value=filing)

    deliveries_result = MagicMock()
    deliveries_result.scalars.return_value.all.return_value = existing_deliveries
    webhooks_result = MagicMock()
    webhooks_result.scalars.return_value.all.return_value = webhooks
    db.execute = AsyncMock(side_effect=[deliveries_result, webhooks_result])
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


def _config(db: AsyncMock) -> dict:
    return {"configurable": {"db": db}}


@pytest.mark.asyncio
async def test_deliver_node_skips_when_briefs_missing() -> None:
    state = PipelineState(filing_id=uuid.uuid4(), raw_text="")
    db = AsyncMock()

    result = await deliver_node(state, _config(db))

    assert result.delivery_status is None
    db.get.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_node_sends_slack_when_configured_and_unsent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X")
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state()
    filing = _make_filing(state.filing_id)
    db = _make_db(filing, existing_deliveries=[], webhooks=[])

    with patch(
        "regradar.agents.delivery_agent.send_slack_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=200)),
    ) as mock_slack:
        result = await deliver_node(state, _config(db))

    mock_slack.assert_awaited_once()
    assert "slack=sent" in result.delivery_status
    added = db.add.call_args_list[0].args[0]
    assert added.channel == DeliveryChannel.SLACK
    assert added.status == DeliveryStatus.SENT


@pytest.mark.asyncio
async def test_deliver_node_skips_slack_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state()
    filing = _make_filing(state.filing_id)
    db = _make_db(filing, existing_deliveries=[], webhooks=[])

    with patch("regradar.agents.delivery_agent.send_slack_alert", new=AsyncMock()) as mock_slack:
        result = await deliver_node(state, _config(db))

    mock_slack.assert_not_awaited()
    assert "slack=not_configured" in result.delivery_status
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_node_does_not_resend_already_sent_slack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X")
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state()
    filing = _make_filing(state.filing_id)
    existing = MagicMock()
    existing.channel = DeliveryChannel.SLACK
    existing.webhook_id = None
    db = _make_db(filing, existing_deliveries=[existing], webhooks=[])

    with patch("regradar.agents.delivery_agent.send_slack_alert", new=AsyncMock()) as mock_slack:
        result = await deliver_node(state, _config(db))

    mock_slack.assert_not_awaited()
    assert "slack=" not in (result.delivery_status or "")


@pytest.mark.asyncio
async def test_deliver_node_records_failed_row_when_slack_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X")
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state()
    filing = _make_filing(state.filing_id)
    db = _make_db(filing, existing_deliveries=[], webhooks=[])

    with patch(
        "regradar.agents.delivery_agent.send_slack_alert",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await deliver_node(state, _config(db))

    added = db.add.call_args_list[0].args[0]
    assert added.status == DeliveryStatus.FAILED
    assert added.response_code is None
    assert "slack=failed" in result.delivery_status


@pytest.mark.asyncio
async def test_deliver_node_uses_state_risk_level_not_stale_filing_risk_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The filing row fetched inside deliver_node has stale domain/risk_level
    (pipeline_tasks.py only sets them AFTER ainvoke() returns) — deliver_node
    must build content from state.risk_level, not filing.risk_level."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X")
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state(risk_level=RiskLevel.CRITICAL)
    filing = _make_filing(state.filing_id)
    filing.risk_level = None  # stale DB value — this run hasn't been persisted yet
    db = _make_db(filing, existing_deliveries=[], webhooks=[])

    with patch(
        "regradar.agents.delivery_agent.send_slack_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=200)),
    ) as mock_slack:
        await deliver_node(state, _config(db))

    call_kwargs = mock_slack.call_args.kwargs
    assert call_kwargs["risk_level"] == RiskLevel.CRITICAL


@pytest.mark.asyncio
async def test_deliver_node_sends_to_matching_active_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state(risk_level=RiskLevel.HIGH)
    filing = _make_filing(state.filing_id)
    webhook = MagicMock(spec=Webhook)
    webhook.id = uuid.uuid4()
    webhook.url = "https://example.com/hook"
    webhook.hmac_secret = "secret"
    webhook.is_active = True
    webhook.filter_domain = None
    webhook.filter_min_risk = None
    db = _make_db(filing, existing_deliveries=[], webhooks=[webhook])

    with patch(
        "regradar.agents.delivery_agent.send_webhook_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=200)),
    ) as mock_webhook:
        result = await deliver_node(state, _config(db))

    mock_webhook.assert_awaited_once()
    added = db.add.call_args_list[0].args[0]
    assert added.channel == DeliveryChannel.WEBHOOK
    assert added.webhook_id == webhook.id
    assert f"webhook:{webhook.id}=sent" in result.delivery_status


@pytest.mark.asyncio
async def test_deliver_node_skips_webhook_with_non_matching_filter_min_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state(risk_level=RiskLevel.LOW)
    filing = _make_filing(state.filing_id)
    webhook = MagicMock(spec=Webhook)
    webhook.id = uuid.uuid4()
    webhook.url = "https://example.com/hook"
    webhook.hmac_secret = "secret"
    webhook.is_active = True
    webhook.filter_domain = None
    webhook.filter_min_risk = RiskLevel.HIGH
    db = _make_db(filing, existing_deliveries=[], webhooks=[webhook])

    with patch(
        "regradar.agents.delivery_agent.send_webhook_alert", new=AsyncMock()
    ) as mock_webhook:
        result = await deliver_node(state, _config(db))

    mock_webhook.assert_not_awaited()
    # Slack/SendGrid are also unconfigured in this env, so delivery_status
    # legitimately contains "slack=not_configured, email=not_configured"
    # (see test_deliver_node_skips_slack_when_not_configured for that
    # behavior's dedicated coverage) — what this test actually verifies is
    # that the non-matching webhook was skipped and recorded nothing.
    assert f"webhook:{webhook.id}=" not in (result.delivery_status or "")


@pytest.mark.asyncio
async def test_deliver_node_records_failed_row_on_webhook_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state()
    filing = _make_filing(state.filing_id)
    webhook = MagicMock(spec=Webhook)
    webhook.id = uuid.uuid4()
    webhook.url = "https://internal.example.com/hook"
    webhook.hmac_secret = "secret"
    webhook.is_active = True
    webhook.filter_domain = None
    webhook.filter_min_risk = None
    db = _make_db(filing, existing_deliveries=[], webhooks=[webhook])

    from regradar.delivery.webhook_dispatcher import WebhookValidationError

    with patch(
        "regradar.agents.delivery_agent.send_webhook_alert",
        new=AsyncMock(side_effect=WebhookValidationError("private IP")),
    ):
        result = await deliver_node(state, _config(db))

    added = db.add.call_args_list[0].args[0]
    assert added.status == DeliveryStatus.FAILED
    assert f"webhook:{webhook.id}=failed" in result.delivery_status


@pytest.mark.asyncio
async def test_deliver_node_one_channel_failure_does_not_block_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X")
    monkeypatch.setenv("SENDGRID_API_KEY", "sg-test-key")
    monkeypatch.setenv("DELIVERY_EMAIL_RECIPIENT", "alerts@example.com")
    state = _make_state()
    filing = _make_filing(state.filing_id)
    db = _make_db(filing, existing_deliveries=[], webhooks=[])

    with (
        patch(
            "regradar.agents.delivery_agent.send_slack_alert",
            new=AsyncMock(side_effect=RuntimeError("slack down")),
        ),
        patch(
            "regradar.agents.delivery_agent.send_email_alert",
            new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=202)),
        ) as mock_email,
    ):
        result = await deliver_node(state, _config(db))

    mock_email.assert_awaited_once()
    assert "slack=failed" in result.delivery_status
    assert "email=sent" in result.delivery_status
