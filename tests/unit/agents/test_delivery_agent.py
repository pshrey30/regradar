"""Unit tests for the Delivery Agent's fan-out, idempotency, and filtering logic.

All HTTP clients (Slack/SendGrid/webhook) are mocked at the send_*_alert
function boundary — no real network calls. The DB session is an AsyncMock,
matching the pattern in test_pipeline_tasks.py. Slack "configured" is
expressed via an OrganizationDeliverySettings row (DELIV-01: per-org, not
a global env var) returned from the second db.get() call.
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
os.environ.setdefault("GROQ_API_KEY", "test")
os.environ.setdefault("SEC_EDGAR_USER_AGENT", "RegRadar/1.0 (test@example.com)")

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from regradar.agents.delivery_agent import deliver_node
from regradar.agents.state import BriefSet, PipelineState
from regradar.delivery.types import DeliveryResult
from regradar.models.enums import DeliveryChannel, DeliveryStatus, FilingDomain, RiskLevel
from regradar.models.filing import Filing
from regradar.models.organization_delivery_settings import OrganizationDeliverySettings
from regradar.models.webhook import Webhook

_SLACK_URL = "https://hooks.slack.com/services/T/B/X"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from regradar.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _mock_redis_client():
    """DELIV-04's Slack-failure-streak tracking needs a Redis client —
    faked here so every existing test doesn't need a real one."""
    mock_client = AsyncMock()
    mock_client.incr = AsyncMock(return_value=1)
    mock_client.expire = AsyncMock()
    mock_client.delete = AsyncMock()
    with patch("regradar.agents.delivery_agent.get_redis_client", return_value=mock_client):
        yield mock_client


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
    filing.organization_id = uuid.uuid4()
    filing.entity_name = "Acme Corp"
    filing.filing_type = "10-K"
    filing.filing_url = "https://example.com/filing"
    return filing


def _make_delivery_settings(slack_webhook_url: str | None) -> MagicMock:
    settings_row = MagicMock(spec=OrganizationDeliverySettings)
    settings_row.slack_webhook_url = slack_webhook_url
    return settings_row


def _make_db(
    filing: MagicMock,
    existing_deliveries: list,
    webhooks: list,
    *,
    delivery_settings: MagicMock | None = None,
    role_settings: MagicMock | None = None,
) -> AsyncMock:
    db = AsyncMock()
    # ORG-11's role fan-out unconditionally looks up
    # OrganizationRoleDeliverySettings for each role_for_domain(state.domain)
    # entry (state.domain defaults to FilingDomain.FINANCIAL in _make_state,
    # which maps to exactly one role, ANALYST) even when state.relevance is
    # None — it `continue`s only after that lookup. A real DB would just
    # return None for an unconfigured role, so this third db.get() value
    # models that, keeping every pre-existing test's assertions unchanged.
    db.get = AsyncMock(side_effect=[filing, delivery_settings, role_settings])

    deliveries_result = MagicMock()
    deliveries_result.scalars.return_value.all.return_value = existing_deliveries
    webhooks_result = MagicMock()
    webhooks_result.scalars.return_value.all.return_value = webhooks
    query_results = iter([deliveries_result, webhooks_result])

    async def _execute(stmt, *args, **kwargs):
        # set_rls_context re-asserts the RLS GUCs before every _record_delivery
        # commit (see delivery_agent.py's comment) — those set_config calls
        # aren't real queries and must not consume the deliveries/webhooks
        # query_results queue.
        if "set_config" in getattr(stmt, "text", ""):
            return MagicMock()
        return next(query_results)

    db.execute = AsyncMock(side_effect=_execute)
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
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state()
    filing = _make_filing(state.filing_id)
    db = _make_db(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=_make_delivery_settings(_SLACK_URL),
    )

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
    # The persisted recipient must be a stable, non-secret identifier —
    # never the real Slack webhook URL (which is a bearer credential).
    assert added.recipient == "slack:default"


@pytest.mark.asyncio
async def test_deliver_node_skips_slack_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state()
    filing = _make_filing(state.filing_id)
    db = _make_db(filing, existing_deliveries=[], webhooks=[], delivery_settings=None)

    with patch("regradar.agents.delivery_agent.send_slack_alert", new=AsyncMock()) as mock_slack:
        result = await deliver_node(state, _config(db))

    mock_slack.assert_not_awaited()
    assert "slack=not_configured" in result.delivery_status
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_node_skips_slack_when_org_row_exists_but_url_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An organization_delivery_settings row can exist with slack_webhook_url
    left null (an org that's configured email but not Slack) — that must
    behave identically to no row existing at all, not crash on None."""
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state()
    filing = _make_filing(state.filing_id)
    db = _make_db(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=_make_delivery_settings(None),
    )

    with patch("regradar.agents.delivery_agent.send_slack_alert", new=AsyncMock()) as mock_slack:
        result = await deliver_node(state, _config(db))

    mock_slack.assert_not_awaited()
    assert "slack=not_configured" in result.delivery_status


@pytest.mark.asyncio
async def test_deliver_node_does_not_resend_already_sent_slack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state()
    filing = _make_filing(state.filing_id)
    existing = MagicMock()
    existing.channel = DeliveryChannel.SLACK
    existing.webhook_id = None
    db = _make_db(
        filing,
        existing_deliveries=[existing],
        webhooks=[],
        delivery_settings=_make_delivery_settings(_SLACK_URL),
    )

    with patch("regradar.agents.delivery_agent.send_slack_alert", new=AsyncMock()) as mock_slack:
        result = await deliver_node(state, _config(db))

    mock_slack.assert_not_awaited()
    assert "slack=" not in (result.delivery_status or "")


@pytest.mark.asyncio
async def test_deliver_node_records_failed_row_when_slack_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state()
    filing = _make_filing(state.filing_id)
    db = _make_db(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=_make_delivery_settings(_SLACK_URL),
    )

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
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state(risk_level=RiskLevel.CRITICAL)
    filing = _make_filing(state.filing_id)
    filing.risk_level = None  # stale DB value — this run hasn't been persisted yet
    db = _make_db(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=_make_delivery_settings(_SLACK_URL),
    )

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
    db = _make_db(filing, existing_deliveries=[], webhooks=[webhook], delivery_settings=None)

    with patch(
        "regradar.agents.delivery_agent.send_webhook_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=200)),
    ) as mock_webhook:
        result = await deliver_node(state, _config(db))

    mock_webhook.assert_awaited_once()
    added = db.add.call_args_list[0].args[0]
    assert added.channel == DeliveryChannel.WEBHOOK
    assert added.webhook_id == webhook.id
    assert added.organization_id == filing.organization_id
    assert f"webhook:{webhook.id}=sent" in result.delivery_status


@pytest.mark.asyncio
async def test_deliver_node_scopes_webhook_query_to_filings_own_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-05 real bug: `service` bypasses RLS org-scoping (it needs
    cross-org write access), so deliver_node's own webhook query must
    filter by organization_id explicitly — otherwise a filing would fan
    out to every organization's registered webhooks, not just its own."""
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state(risk_level=RiskLevel.HIGH)
    filing = _make_filing(state.filing_id)
    db = _make_db(filing, existing_deliveries=[], webhooks=[], delivery_settings=None)

    await deliver_node(state, _config(db))

    # The webhook re-assertion fix (final whole-branch review) adds a
    # set_rls_context call -- and its underlying set_config execute() --
    # immediately before this query, so find the webhook SELECT by content
    # rather than assuming a fixed call index.
    webhooks_call = next(
        call
        for call in db.execute.call_args_list
        if "set_config" not in getattr(call.args[0], "text", "") and "webhooks" in str(call.args[0]).lower()
    )
    compiled = str(webhooks_call.args[0].compile(compile_kwargs={"literal_binds": True}))
    # literal_binds renders a UUID without dashes — compare the hex form.
    assert filing.organization_id.hex in compiled


@pytest.mark.asyncio
async def test_deliver_node_skips_webhook_with_non_matching_filter_min_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    db = _make_db(filing, existing_deliveries=[], webhooks=[webhook], delivery_settings=None)

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
    db = _make_db(filing, existing_deliveries=[], webhooks=[webhook], delivery_settings=None)

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
    monkeypatch.setenv("SENDGRID_API_KEY", "sg-test-key")
    monkeypatch.setenv("DELIVERY_EMAIL_RECIPIENT", "alerts@example.com")
    state = _make_state()
    filing = _make_filing(state.filing_id)
    db = _make_db(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=_make_delivery_settings(_SLACK_URL),
    )

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


@pytest.mark.asyncio
async def test_deliver_node_delivery_success_false_when_nothing_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When every configured channel fails (or nothing is configured/matches),
    delivery_success must be False (a definite "ran but sent nothing"
    signal), not None (which is reserved for "didn't run at all")."""
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state()
    filing = _make_filing(state.filing_id)
    db = _make_db(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=_make_delivery_settings(_SLACK_URL),
    )

    with patch(
        "regradar.agents.delivery_agent.send_slack_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.FAILED, response_code=500)),
    ):
        result = await deliver_node(state, _config(db))

    assert result.delivery_status is not None
    assert result.delivery_success is False


@pytest.mark.asyncio
async def test_deliver_node_delivery_success_true_when_one_channel_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state()
    filing = _make_filing(state.filing_id)
    db = _make_db(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=_make_delivery_settings(_SLACK_URL),
    )

    with patch(
        "regradar.agents.delivery_agent.send_slack_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=200)),
    ):
        result = await deliver_node(state, _config(db))

    assert result.delivery_success is True


# --- DELIV-04: Slack failure fallback ---


@pytest.mark.asyncio
async def test_deliver_node_falls_back_to_email_when_slack_fails_and_no_primary_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.setenv("ADMIN_FALLBACK_EMAIL", "admin@example.com")
    state = _make_state(risk_level=RiskLevel.HIGH)
    filing = _make_filing(state.filing_id)
    db = _make_db(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=_make_delivery_settings(_SLACK_URL),
    )

    with (
        patch(
            "regradar.agents.delivery_agent.send_slack_alert",
            new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.FAILED, response_code=500)),
        ),
        patch(
            "regradar.agents.delivery_agent.send_email_alert",
            new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=202)),
        ) as mock_email,
    ):
        result = await deliver_node(state, _config(db))

    mock_email.assert_awaited_once()
    assert mock_email.call_args.kwargs["recipient"] == "admin@example.com"
    assert "email_fallback=sent" in result.delivery_status
    fallback_rows = [
        call.args[0] for call in db.add.call_args_list if call.args[0].is_fallback
    ]
    assert len(fallback_rows) == 1
    assert fallback_rows[0].channel == DeliveryChannel.EMAIL
    assert fallback_rows[0].recipient == "admin@example.com"
    assert fallback_rows[0].status == DeliveryStatus.SENT


@pytest.mark.asyncio
async def test_deliver_node_falls_back_to_email_when_slack_not_configured_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.setenv("ADMIN_FALLBACK_EMAIL", "admin@example.com")
    state = _make_state(risk_level=RiskLevel.CRITICAL)
    filing = _make_filing(state.filing_id)
    db = _make_db(filing, existing_deliveries=[], webhooks=[], delivery_settings=None)

    with patch(
        "regradar.agents.delivery_agent.send_email_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=202)),
    ) as mock_email:
        result = await deliver_node(state, _config(db))

    mock_email.assert_awaited_once()
    assert "slack=not_configured" in result.delivery_status
    assert "email_fallback=sent" in result.delivery_status


@pytest.mark.asyncio
async def test_deliver_node_no_fallback_when_primary_email_already_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal email channel already attempts independently of Slack's
    outcome — a fallback here would just be a duplicate send."""
    monkeypatch.setenv("SENDGRID_API_KEY", "sg-test-key")
    monkeypatch.setenv("DELIVERY_EMAIL_RECIPIENT", "alerts@example.com")
    monkeypatch.setenv("ADMIN_FALLBACK_EMAIL", "admin@example.com")
    state = _make_state(risk_level=RiskLevel.HIGH)
    filing = _make_filing(state.filing_id)
    db = _make_db(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=_make_delivery_settings(_SLACK_URL),
    )

    with (
        patch(
            "regradar.agents.delivery_agent.send_slack_alert",
            new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.FAILED, response_code=500)),
        ),
        patch(
            "regradar.agents.delivery_agent.send_email_alert",
            new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=202)),
        ) as mock_email,
    ):
        result = await deliver_node(state, _config(db))

    mock_email.assert_awaited_once()  # the normal email branch's one call, not a fallback
    assert "email_fallback" not in result.delivery_status
    assert "email=sent" in result.delivery_status
    assert not any(call.args[0].is_fallback for call in db.add.call_args_list)


@pytest.mark.asyncio
async def test_deliver_node_no_fallback_for_low_risk_filing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.setenv("ADMIN_FALLBACK_EMAIL", "admin@example.com")
    state = _make_state(risk_level=RiskLevel.LOW)
    filing = _make_filing(state.filing_id)
    db = _make_db(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=_make_delivery_settings(_SLACK_URL),
    )

    with (
        patch(
            "regradar.agents.delivery_agent.send_slack_alert",
            new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.FAILED, response_code=500)),
        ),
        patch("regradar.agents.delivery_agent.send_email_alert", new=AsyncMock()) as mock_email,
    ):
        result = await deliver_node(state, _config(db))

    mock_email.assert_not_awaited()
    assert "email_fallback" not in result.delivery_status


@pytest.mark.asyncio
async def test_deliver_node_fallback_not_configured_when_admin_fallback_email_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.delenv("ADMIN_FALLBACK_EMAIL", raising=False)
    state = _make_state(risk_level=RiskLevel.CRITICAL)
    filing = _make_filing(state.filing_id)
    db = _make_db(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=_make_delivery_settings(_SLACK_URL),
    )

    with (
        patch(
            "regradar.agents.delivery_agent.send_slack_alert",
            new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.FAILED, response_code=500)),
        ),
        patch("regradar.agents.delivery_agent.send_email_alert", new=AsyncMock()) as mock_email,
    ):
        result = await deliver_node(state, _config(db))

    mock_email.assert_not_awaited()
    assert "email_fallback=not_configured" in result.delivery_status


@pytest.mark.asyncio
async def test_deliver_node_increments_slack_failure_streak_on_failure(
    monkeypatch: pytest.MonkeyPatch, _mock_redis_client: AsyncMock
) -> None:
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state(risk_level=RiskLevel.LOW)  # risk level irrelevant to streak tracking
    filing = _make_filing(state.filing_id)
    db = _make_db(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=_make_delivery_settings(_SLACK_URL),
    )

    with patch(
        "regradar.agents.delivery_agent.send_slack_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.FAILED, response_code=500)),
    ):
        await deliver_node(state, _config(db))

    _mock_redis_client.incr.assert_awaited_once_with(f"slack_failure_streak:{filing.organization_id}")
    _mock_redis_client.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_node_resets_slack_failure_streak_on_success(
    monkeypatch: pytest.MonkeyPatch, _mock_redis_client: AsyncMock
) -> None:
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state()
    filing = _make_filing(state.filing_id)
    db = _make_db(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=_make_delivery_settings(_SLACK_URL),
    )

    with patch(
        "regradar.agents.delivery_agent.send_slack_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=200)),
    ):
        await deliver_node(state, _config(db))

    _mock_redis_client.delete.assert_awaited_once_with(f"slack_failure_streak:{filing.organization_id}")
    _mock_redis_client.incr.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_node_does_not_touch_streak_when_slack_not_configured(
    monkeypatch: pytest.MonkeyPatch, _mock_redis_client: AsyncMock
) -> None:
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    state = _make_state(risk_level=RiskLevel.LOW)
    filing = _make_filing(state.filing_id)
    db = _make_db(filing, existing_deliveries=[], webhooks=[], delivery_settings=None)

    await deliver_node(state, _config(db))

    _mock_redis_client.incr.assert_not_awaited()
    _mock_redis_client.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_slack_failure_logs_reconnection_needed_at_threshold(
    _mock_redis_client: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    from regradar.agents.delivery_agent import (
        _SLACK_RECONNECTION_THRESHOLD,
        _record_slack_failure_and_maybe_notify,
    )

    org_id = uuid.uuid4()
    _mock_redis_client.incr = AsyncMock(return_value=_SLACK_RECONNECTION_THRESHOLD)

    with caplog.at_level("ERROR", logger="regradar.agents.delivery_agent"):
        await _record_slack_failure_and_maybe_notify(org_id)

    assert any("Slack reconnection needed" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_record_slack_failure_does_not_log_below_threshold(
    _mock_redis_client: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    from regradar.agents.delivery_agent import _record_slack_failure_and_maybe_notify

    org_id = uuid.uuid4()
    _mock_redis_client.incr = AsyncMock(return_value=1)

    with caplog.at_level("ERROR", logger="regradar.agents.delivery_agent"):
        await _record_slack_failure_and_maybe_notify(org_id)

    assert not any("Slack reconnection needed" in record.message for record in caplog.records)


# --- ORG-11: role-routed alert fan-out ---

from regradar.agents.state import RelevanceResult
from regradar.models.organization_role_delivery_settings import OrganizationRoleDeliverySettings


def _make_role_settings(
    *, slack_webhook_url: str | None = None, email: str | None = None
) -> MagicMock:
    settings_row = MagicMock(spec=OrganizationRoleDeliverySettings)
    settings_row.slack_webhook_url = slack_webhook_url
    settings_row.email = email
    return settings_row


def _make_db_with_role_settings(
    filing: MagicMock,
    existing_deliveries: list,
    webhooks: list,
    *,
    delivery_settings: MagicMock | None = None,
    role_settings: MagicMock | None = None,
) -> AsyncMock:
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[filing, delivery_settings, role_settings])

    deliveries_result = MagicMock()
    deliveries_result.scalars.return_value.all.return_value = existing_deliveries
    webhooks_result = MagicMock()
    webhooks_result.scalars.return_value.all.return_value = webhooks
    query_results = iter([deliveries_result, webhooks_result])

    async def _execute(stmt, *args, **kwargs):
        if "set_config" in getattr(stmt, "text", ""):
            return MagicMock()
        return next(query_results)

    db.execute = AsyncMock(side_effect=_execute)
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_deliver_node_routes_engineering_filing_to_eng_lead_slack() -> None:
    filing_id = uuid.uuid4()
    filing = _make_filing(filing_id)
    state = _make_state(risk_level=RiskLevel.HIGH)
    state = state.model_copy(
        update={
            "domain": FilingDomain.ENGINEERING,
            "relevance": RelevanceResult(
                relevance_score=0.9,
                matched_signals={"products": ["widget"]},
                rationale="This affects your widget product line.",
                recommended_action="Audit your widget suppliers.",
            ),
        }
    )
    role_settings = _make_role_settings(slack_webhook_url=_SLACK_URL)
    db = _make_db_with_role_settings(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=None,
        role_settings=role_settings,
    )

    with patch(
        "regradar.agents.delivery_agent.send_slack_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=200)),
    ) as mock_send_slack:
        await deliver_node(state, config={"configurable": {"db": db}})

    mock_send_slack.assert_awaited_once()
    call_kwargs = mock_send_slack.call_args.kwargs
    assert call_kwargs["webhook_url"] == _SLACK_URL
    assert "widget" in call_kwargs["cco_summary"]
    assert "Audit your widget suppliers" in call_kwargs["cco_summary"]


@pytest.mark.asyncio
async def test_deliver_node_skips_role_fanout_when_no_role_settings_configured() -> None:
    filing_id = uuid.uuid4()
    filing = _make_filing(filing_id)
    state = _make_state(risk_level=RiskLevel.HIGH)
    state = state.model_copy(update={"domain": FilingDomain.ENGINEERING, "relevance": None})
    db = _make_db_with_role_settings(
        filing, existing_deliveries=[], webhooks=[], delivery_settings=None, role_settings=None
    )

    with patch(
        "regradar.agents.delivery_agent.send_slack_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=200)),
    ) as mock_send_slack:
        await deliver_node(state, config={"configurable": {"db": db}})

    mock_send_slack.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_node_role_fanout_is_additive_to_org_wide_slack() -> None:
    filing_id = uuid.uuid4()
    filing = _make_filing(filing_id)
    state = _make_state(risk_level=RiskLevel.HIGH)
    state = state.model_copy(
        update={
            "domain": FilingDomain.ENGINEERING,
            "relevance": RelevanceResult(
                relevance_score=0.9,
                matched_signals={},
                rationale="Matches your product line.",
                recommended_action="Investigate.",
            ),
        }
    )
    org_wide_settings = _make_delivery_settings(_SLACK_URL)
    role_settings = _make_role_settings(slack_webhook_url="https://hooks.slack.com/services/ROLE")
    db = _make_db_with_role_settings(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=org_wide_settings,
        role_settings=role_settings,
    )

    with patch(
        "regradar.agents.delivery_agent.send_slack_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=200)),
    ) as mock_send_slack:
        await deliver_node(state, config={"configurable": {"db": db}})

    assert mock_send_slack.await_count == 2
    sent_urls = {call.kwargs["webhook_url"] for call in mock_send_slack.await_args_list}
    assert sent_urls == {_SLACK_URL, "https://hooks.slack.com/services/ROLE"}


@pytest.mark.asyncio
async def test_deliver_node_role_fanout_email_uses_role_email_recipient() -> None:
    filing_id = uuid.uuid4()
    filing = _make_filing(filing_id)
    state = _make_state(risk_level=RiskLevel.HIGH)
    state = state.model_copy(
        update={
            "domain": FilingDomain.FINANCIAL,
            "relevance": RelevanceResult(
                relevance_score=0.7,
                matched_signals={},
                rationale="Relevant to your reporting obligations.",
                recommended_action="Review your Q3 filing.",
            ),
        }
    )
    role_settings = _make_role_settings(email="analyst-team@example.com")
    db = _make_db_with_role_settings(
        filing, existing_deliveries=[], webhooks=[], delivery_settings=None, role_settings=role_settings
    )

    with patch(
        "regradar.agents.delivery_agent.send_email_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=202)),
    ) as mock_send_email:
        await deliver_node(state, config={"configurable": {"db": db}})

    mock_send_email.assert_awaited_once()
    assert mock_send_email.call_args.kwargs["recipient"] == "analyst-team@example.com"


@pytest.mark.asyncio
async def test_deliver_node_reasserts_rls_context_before_role_fanout_lookup() -> None:
    """Regression test for the Critical bug found in Task 11 live verification:
    _record_delivery commits after every channel, and Postgres' set_config(...,
    true) is transaction-scoped, so any earlier commit (e.g. the org-wide Slack
    block above the role fan-out) silently reverts app.current_role before the
    role fan-out's db.get(OrganizationRoleDeliverySettings, ...) lookup runs.
    Without a fresh set_rls_context(db, role="service") call right before the
    role loop, that lookup returns None for a real, correctly-configured row.
    This asserts the re-assertion happens, in the right order relative to the
    earlier org-wide delivery's commit and the role-settings lookup.
    """
    filing_id = uuid.uuid4()
    filing = _make_filing(filing_id)
    state = _make_state(risk_level=RiskLevel.HIGH)
    state = state.model_copy(
        update={
            "domain": FilingDomain.ENGINEERING,
            "relevance": RelevanceResult(
                relevance_score=0.9,
                matched_signals={},
                rationale="Matches your product line.",
                recommended_action="Investigate.",
            ),
        }
    )
    org_wide_settings = _make_delivery_settings(_SLACK_URL)
    role_settings = _make_role_settings(slack_webhook_url="https://hooks.slack.com/services/ROLE")
    db = _make_db_with_role_settings(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=org_wide_settings,
        role_settings=role_settings,
    )

    call_order: list[str] = []
    original_db_get = db.get

    async def _tracking_get(model, pk):
        result = await original_db_get(model, pk)
        if model is OrganizationRoleDeliverySettings:
            call_order.append("db.get:role_settings")
        return result

    db.get = AsyncMock(side_effect=_tracking_get)
    db.commit = AsyncMock(side_effect=lambda: call_order.append("db.commit"))

    async def _tracking_set_rls_context(*args, **kwargs):
        call_order.append("set_rls_context")

    with (
        patch(
            "regradar.agents.delivery_agent.send_slack_alert",
            new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=200)),
        ),
        patch(
            "regradar.agents.delivery_agent.set_rls_context",
            new=AsyncMock(side_effect=_tracking_set_rls_context),
        ) as mock_set_rls_context,
    ):
        await deliver_node(state, config={"configurable": {"db": db}})

    # The org-wide Slack block commits (via _record_delivery) before the role
    # fan-out's lookup even starts — that earlier commit is what silently
    # reverts the RLS GUC in real Postgres.
    commit_index = call_order.index("db.commit")
    role_lookup_index = call_order.index("db.get:role_settings")
    assert commit_index < role_lookup_index

    # set_rls_context must be re-asserted strictly between that commit and the
    # role-settings lookup — this is exactly the fix for the bug.
    reasserted_between = any(
        event == "set_rls_context" and commit_index < i < role_lookup_index
        for i, event in enumerate(call_order)
    )
    assert reasserted_between, (
        "set_rls_context was not re-asserted after the org-wide delivery's commit "
        "and before the role fan-out's db.get(OrganizationRoleDeliverySettings) lookup "
        "-- this is the exact bug found in Task 11 live verification."
    )
    assert mock_set_rls_context.await_count >= 1


@pytest.mark.asyncio
async def test_deliver_node_reasserts_rls_context_before_webhook_fanout_lookup() -> None:
    """Regression test for the pre-existing bug found in the final whole-branch
    review: the webhook fan-out's db.execute(select(Webhook)...) read has no
    set_rls_context re-assertion before it, even though it runs after the
    Slack block above (which commits via _record_delivery). Postgres'
    set_config(..., true) is transaction-scoped, so that earlier commit
    silently reverts app.current_role to '' -- and webhooks_select's RLS
    policy (owner/admin/service only) then filters the SELECT to zero rows
    instead of erroring, so a fully-configured org gets no webhook fan-out at
    all. This asserts set_rls_context is re-asserted strictly between the
    Slack block's commit and the webhook query.
    """
    filing_id = uuid.uuid4()
    filing = _make_filing(filing_id)
    state = _make_state(risk_level=RiskLevel.HIGH)

    delivery_settings = _make_delivery_settings(_SLACK_URL)
    db = _make_db(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=delivery_settings,
        role_settings=None,
    )

    call_order: list[str] = []
    original_db_execute = db.execute

    async def _tracking_execute(stmt, *args, **kwargs):
        result = await original_db_execute(stmt, *args, **kwargs)
        if "set_config" not in getattr(stmt, "text", "") and "webhooks" in str(stmt).lower():
            call_order.append("db.execute:webhooks")
        return result

    db.execute = AsyncMock(side_effect=_tracking_execute)
    db.commit = AsyncMock(side_effect=lambda: call_order.append("db.commit"))

    async def _tracking_set_rls_context(*args, **kwargs):
        call_order.append("set_rls_context")

    with (
        patch(
            "regradar.agents.delivery_agent.send_slack_alert",
            new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=200)),
        ),
        patch(
            "regradar.agents.delivery_agent.set_rls_context",
            new=AsyncMock(side_effect=_tracking_set_rls_context),
        ) as mock_set_rls_context,
    ):
        await deliver_node(state, config={"configurable": {"db": db}})

    # The Slack block commits (via _record_delivery) before the webhook
    # fan-out's query even starts -- that earlier commit is what silently
    # reverts the RLS GUC in real Postgres.
    commit_index = call_order.index("db.commit")
    webhook_query_index = call_order.index("db.execute:webhooks")
    assert commit_index < webhook_query_index

    # set_rls_context must be re-asserted strictly between that commit and the
    # webhook query -- this is exactly the fix for the bug.
    reasserted_between = any(
        event == "set_rls_context" and commit_index < i < webhook_query_index
        for i, event in enumerate(call_order)
    )
    assert reasserted_between, (
        "set_rls_context was not re-asserted after the Slack block's commit and "
        "before the webhook fan-out's db.execute(select(Webhook)) lookup -- this "
        "is the pre-existing bug found in the final whole-branch review."
    )
    assert mock_set_rls_context.await_count >= 1
