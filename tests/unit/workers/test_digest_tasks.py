"""Unit tests for DELIV-03's weekly digest job — no real DB or SendGrid calls."""

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
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import regradar.workers.digest_tasks as digest_tasks_module
from regradar.delivery.types import DeliveryResult
from regradar.models.enums import DeliveryStatus, RiskLevel
from regradar.workers.digest_tasks import _send_all_digests


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from regradar.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_org(name: str = "Acme Org") -> MagicMock:
    org = MagicMock()
    org.id = uuid.uuid4()
    org.name = name
    return org


def _make_filing(
    entity_name: str,
    risk_level: RiskLevel,
    *,
    executive_brief: str | None = "Filing summary.",
    published_at: datetime | None = None,
) -> MagicMock:
    filing = MagicMock()
    filing.id = uuid.uuid4()
    filing.entity_name = entity_name
    filing.risk_level = risk_level
    filing.published_at = published_at or datetime.now(UTC)
    if executive_brief is None:
        filing.brief = None
    else:
        filing.brief = MagicMock()
        filing.brief.executive_brief = executive_brief
    return filing


def _make_session_factory(orgs: list, filings_by_org: dict) -> MagicMock:
    """One shared mock DB: the first real db.execute() call (in
    _send_all_digests) returns the organizations list; every subsequent
    real call (one per org, in _digest_entries_for_organization) returns
    that org's filings. set_rls_context's own set_config calls (issued
    before each real query) aren't real queries and must not consume this
    queue."""
    db = AsyncMock()

    orgs_result = MagicMock()
    orgs_result.scalars.return_value.all.return_value = orgs

    org_results = []
    for org in orgs:
        result = MagicMock()
        result.scalars.return_value.all.return_value = filings_by_org.get(org.id, [])
        org_results.append(result)

    query_results = iter([orgs_result, *org_results])

    async def _execute(stmt, *args, **kwargs):
        if "set_config" in getattr(stmt, "text", ""):
            return MagicMock()
        return next(query_results)

    db.execute = AsyncMock(side_effect=_execute)

    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return session_factory


@pytest.mark.asyncio
async def test_send_all_digests_skips_entirely_when_no_recipient_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DELIVERY_EMAIL_RECIPIENT", raising=False)

    with patch.object(digest_tasks_module, "get_session_factory") as mock_get_session_factory:
        await _send_all_digests()

    mock_get_session_factory.assert_not_called()


@pytest.mark.asyncio
async def test_send_all_digests_passes_every_matching_filing_to_send_digest_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Risk-descending sort order is send_digest_email's own responsibility
    (see test_sendgrid_client.py) — this checks _send_all_digests hands it
    every matching filing, correctly converted to DigestFilingEntry."""
    monkeypatch.setenv("DELIVERY_EMAIL_RECIPIENT", "digest@example.com")
    org = _make_org("Acme Org")
    high_filing = _make_filing("Acme Corp", RiskLevel.HIGH, executive_brief="High risk filing.")
    critical_filing = _make_filing("Beta Inc", RiskLevel.CRITICAL, executive_brief="Critical filing.")
    session_factory = _make_session_factory(
        orgs=[org], filings_by_org={org.id: [high_filing, critical_filing]}
    )

    with (
        patch.object(digest_tasks_module, "get_session_factory", lambda: session_factory),
        patch.object(
            digest_tasks_module,
            "send_digest_email",
            new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=202)),
        ) as mock_send,
    ):
        await _send_all_digests()

    mock_send.assert_awaited_once()
    call_kwargs = mock_send.call_args.kwargs
    assert call_kwargs["recipient"] == "digest@example.com"
    assert call_kwargs["organization_name"] == "Acme Org"
    entries = call_kwargs["filings"]
    assert {(e.entity_name, e.risk_level) for e in entries} == {
        ("Acme Corp", RiskLevel.HIGH),
        ("Beta Inc", RiskLevel.CRITICAL),
    }


@pytest.mark.asyncio
async def test_send_all_digests_sends_nothing_to_report_when_no_matching_filings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A week with zero Critical/High filings still sends a digest — the
    caller must not skip the organization silently."""
    monkeypatch.setenv("DELIVERY_EMAIL_RECIPIENT", "digest@example.com")
    org = _make_org("Quiet Org")
    session_factory = _make_session_factory(orgs=[org], filings_by_org={org.id: []})

    with (
        patch.object(digest_tasks_module, "get_session_factory", lambda: session_factory),
        patch.object(
            digest_tasks_module,
            "send_digest_email",
            new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=202)),
        ) as mock_send,
    ):
        await _send_all_digests()

    mock_send.assert_awaited_once()
    assert mock_send.call_args.kwargs["filings"] == []


@pytest.mark.asyncio
async def test_send_all_digests_omits_filing_with_no_brief_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELIVERY_EMAIL_RECIPIENT", "digest@example.com")
    org = _make_org()
    no_brief_filing = _make_filing("No Brief Corp", RiskLevel.CRITICAL, executive_brief=None)
    session_factory = _make_session_factory(orgs=[org], filings_by_org={org.id: [no_brief_filing]})

    with (
        patch.object(digest_tasks_module, "get_session_factory", lambda: session_factory),
        patch.object(
            digest_tasks_module,
            "send_digest_email",
            new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=202)),
        ) as mock_send,
    ):
        await _send_all_digests()

    assert mock_send.call_args.kwargs["filings"] == []


@pytest.mark.asyncio
async def test_send_all_digests_sends_one_email_per_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELIVERY_EMAIL_RECIPIENT", "digest@example.com")
    org_a = _make_org("Org A")
    org_b = _make_org("Org B")
    filing_a = _make_filing("Corp A", RiskLevel.HIGH)
    session_factory = _make_session_factory(
        orgs=[org_a, org_b], filings_by_org={org_a.id: [filing_a], org_b.id: []}
    )

    with (
        patch.object(digest_tasks_module, "get_session_factory", lambda: session_factory),
        patch.object(
            digest_tasks_module,
            "send_digest_email",
            new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=202)),
        ) as mock_send,
    ):
        await _send_all_digests()

    assert mock_send.await_count == 2
    org_names_sent = {call.kwargs["organization_name"] for call in mock_send.call_args_list}
    assert org_names_sent == {"Org A", "Org B"}


@pytest.mark.asyncio
async def test_digest_entries_query_excludes_filings_older_than_seven_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELIVERY_EMAIL_RECIPIENT", "digest@example.com")
    org = _make_org()
    # The mock DB doesn't apply real WHERE filtering, so this test asserts
    # the *query itself* carries the published_at lower bound, not just
    # trusting whatever the mock returns.
    session_factory = _make_session_factory(orgs=[org], filings_by_org={org.id: []})

    with (
        patch.object(digest_tasks_module, "get_session_factory", lambda: session_factory),
        patch.object(digest_tasks_module, "send_digest_email", new=AsyncMock()),
    ):
        await _send_all_digests()

    db = session_factory.return_value.__aenter__.return_value
    # set_rls_context issues 2 set_config calls before each real query, so
    # the two real queries (organizations, then this org's filings) land
    # at indices 2 and 5, not 0 and 1.
    real_query_calls = [
        call for call in db.execute.call_args_list if "set_config" not in getattr(call.args[0], "text", "")
    ]
    filings_query_call = real_query_calls[1]
    compiled = str(filings_query_call.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "published_at >=" in compiled
    assert org.id.hex in compiled


def test_send_weekly_digest_task_is_registered() -> None:
    from regradar.workers.celery_app import celery_app
    from regradar.workers.digest_tasks import send_weekly_digest

    assert callable(send_weekly_digest)
    assert "send-weekly-digest" in celery_app.conf.beat_schedule
    assert (
        celery_app.conf.beat_schedule["send-weekly-digest"]["task"]
        == "regradar.workers.digest_tasks.send_weekly_digest"
    )
