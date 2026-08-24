# AGENT-10 — Delivery Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `deliver_node` stub in `agents/graph.py` with a real implementation that fans out a completed filing's brief to Slack, email (SendGrid), and every registered, filter-matching webhook, writing a `deliveries` row per attempt, idempotently (never re-sending a channel already logged as `sent`), with webhook payloads HMAC-signed using each webhook's own secret.

**Architecture:** Three new outbound-delivery client modules under `delivery/` (`slack_client.py`, `sendgrid_client.py`, `webhook_dispatcher.py`), each a thin async `httpx` POST + status-code check returning a shared `DeliveryResult`. A new `agents/delivery_agent.py` owns the real `deliver_node` — unlike every prior node, it both **reads and writes** the database mid-graph (idempotency and webhook fan-out both require a DB read before deciding what to send; multiple `Delivery` rows per filing don't fit the single-row post-graph persistence pattern used for `Extraction`/`Brief`). This extends `retrieve_node`'s existing "async node with `config={"configurable": {"db": db}}`" precedent from reads to writes as well.

**Real deviations from the ticket's literal wording (no organization concept exists in the schema — confirmed via `grep -rn "organization_id" src/regradar/models/` returning zero matches; SEC-05, which would add it, is a separate deferred ticket):** Slack and email are configured as **single global destinations** (`settings.slack_webhook_url`, a new `settings.delivery_email_recipient`) rather than per-organization — there is no org to scope them to yet, consistent with this project's current single-tenant reality. Webhook fan-out is simply "every active `Webhook` row whose `filter_domain`/`filter_min_risk` matches this filing" (webhooks are scoped to `api_key_id`, not an org). SEC-02's webhook SSRF validator is implemented in its minimal dispatch-time form only (the literal function AGENT-10's own AI Coding Prompt calls for) — full registration-time validation belongs to API-08, not built yet. SEC-03's replay-protection timestamp header is explicitly **not** included — that's its own separate ticket; this ticket signs only the raw payload body with the webhook's `hmac_secret`.

**A real cross-node data-flow bug caught during planning, not a request-time symptom:** `deliver_node` runs *inside* `ainvoke()`, before `pipeline_tasks.py`'s post-graph code sets `filing.domain`/`filing.risk_level` on the DB row (those assignments happen only *after* `ainvoke()` returns, using `result["domain"]`/`result["risk_level"]`). So a `Filing` row fetched via `db.get(Filing, state.filing_id)` inside `deliver_node` has **stale** (pre-this-run) `domain`/`risk_level` — reading them there would silently deliver alerts with the wrong or missing risk level and mis-evaluate webhook filters. `deliver_node` must use `state.domain`/`state.risk_level` (populated earlier in the same graph run by `triage_node`) for all classification-derived content, and only use the DB-fetched `Filing` row for the ingestion-time metadata `PipelineState` doesn't carry (`entity_name`, `filing_type`, `filing_url`).

**A second cross-file conflict resolved during planning:** `deliver_node` must **not** set `filing.status` itself. `pipeline_tasks.py`'s existing post-graph code unconditionally re-decides `filing.status` from `result["extraction"]`/`result["briefs"]` after the graph completes — since both nodes would touch the same DB object on the same session (SQLAlchemy's identity map means `db.get(Filing, id)` inside `deliver_node` returns the *same Python object* `pipeline_tasks.py` already holds), any status `deliver_node` set would simply be overwritten by `pipeline_tasks.py`'s unconditional assignment moments later. Resolution: `deliver_node` only ever writes `Delivery` rows; `pipeline_tasks.py` alone decides `filing.status`, now with one more branch — `FilingStatus.COMPLETE` when delivery was attempted (`result["delivery_status"] is not None`) and nothing upstream failed.

**Tech Stack:** Python 3.11, `httpx.AsyncClient` (async, since `deliver_node` is async — ingestion connectors use sync `httpx`, this ticket is the first async-`httpx` call site), Pydantic v2, pytest + `unittest.mock`/`AsyncMock`.

## Global Constraints

- Every delivery attempt — successful or failed — produces exactly one `Delivery` row with the correct `channel`, `status`, and `response_code`. A channel that isn't configured (no Slack webhook URL, no email recipient/SendGrid key) is skipped entirely with **no** row, since no attempt was made.
- Idempotency: before attempting any channel, query existing `Delivery` rows for `filing_id` with `status == DeliveryStatus.SENT`; skip a channel already in that set. Granularity is `(channel, webhook_id)` — `webhook_id` is `None` for Slack/email, the specific webhook's id for webhook deliveries (so each of N registered webhooks is tracked independently).
- Webhook payloads are HMAC-signed using **that specific webhook's own `hmac_secret`** (`Webhook.hmac_secret`), never a shared/global secret — sign with `hmac.new(secret, body, hashlib.<settings.webhook_hmac_algorithm>)`.
- One channel's failure (an exception, not just a non-2xx response) must never prevent the other channels from being attempted — wrap each channel's send in its own try/except, always producing a `FAILED` `Delivery` row on an unexpected exception rather than silently dropping the attempt or crashing the node.
- `deliver_node` must use `state.domain`/`state.risk_level` for all classification-derived content (payloads, webhook filter matching) — never the DB-fetched `Filing.domain`/`Filing.risk_level`, which is stale until `pipeline_tasks.py`'s post-graph code runs. Only `filing.entity_name`/`filing.filing_type`/`filing.filing_url` come from the DB fetch.
- `deliver_node` never sets `filing.status` or commits a status change — that responsibility stays entirely in `pipeline_tasks.py`, per the resolved conflict above.
- No new Alembic migration — `Delivery`/`Webhook` tables and `DeliveryChannel`/`DeliveryStatus` enums already exist from FOUND-02.
- Reuse `settings.slack_webhook_url`, `settings.sendgrid_api_key`, `settings.sendgrid_from_email`, `settings.webhook_hmac_algorithm` (all already defined in `core/config.py`) — only one new config field this ticket adds: `delivery_email_recipient`.

---

## Task 1: Slack and SendGrid delivery clients

**Files:**
- Create: `src/regradar/delivery/types.py`
- Create: `src/regradar/delivery/slack_client.py`
- Create: `src/regradar/delivery/sendgrid_client.py`
- Modify: `src/regradar/core/config.py`
- Modify: `.env.example`
- Create: `tests/unit/delivery/test_slack_client.py`
- Create: `tests/unit/delivery/test_sendgrid_client.py`

**Interfaces:**
- Consumes: `get_settings()` (existing fields `sendgrid_api_key: SecretStr | None`, `sendgrid_from_email: str`; new field this task adds: `delivery_email_recipient: str | None`), `RiskLevel`/`DeliveryStatus` from `regradar.models.enums`.
- Produces: `DeliveryResult` (Pydantic model: `status: DeliveryStatus`, `response_code: int | None`) from `regradar.delivery.types`, consumed by every client and by Task 3's `delivery_agent.py`. `send_slack_alert(webhook_url: str, entity_name: str, filing_type: str, filing_url: str, risk_level: RiskLevel | None, cco_summary: str) -> DeliveryResult` from `regradar.delivery.slack_client`. `send_email_alert(recipient: str, entity_name: str, filing_type: str, risk_level: RiskLevel | None, executive_brief: str) -> DeliveryResult` from `regradar.delivery.sendgrid_client`.

- [ ] **Step 1: Add the new config field**

In `src/regradar/core/config.py`, find the `── Delivery ─────` block:

```python
    slack_webhook_url: SecretStr | None = Field(default=None, alias="SLACK_WEBHOOK_URL")
    slack_bot_token: SecretStr | None = Field(default=None, alias="SLACK_BOT_TOKEN")
    sendgrid_api_key: SecretStr | None = Field(default=None, alias="SENDGRID_API_KEY")
    sendgrid_from_email: str = Field(default="alerts@regradar.io", alias="SENDGRID_FROM_EMAIL")
    webhook_hmac_algorithm: str = Field(default="sha256", alias="WEBHOOK_HMAC_ALGORITHM")
```

Add a new field immediately after `sendgrid_from_email`:

```python
    delivery_email_recipient: str | None = Field(default=None, alias="DELIVERY_EMAIL_RECIPIENT")
```

Add `DELIVERY_EMAIL_RECIPIENT=` to `.env.example` right after the existing `SENDGRID_FROM_EMAIL=` line (populated with a real placeholder like every other non-secret field in that file, matching the file's established style — e.g. `DELIVERY_EMAIL_RECIPIENT=alerts@example.com`).

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/delivery/test_slack_client.py`:

```python
"""Unit tests for the Slack delivery client — no real network calls."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from regradar.delivery.slack_client import send_slack_alert
from regradar.models.enums import DeliveryStatus, RiskLevel


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
```

Create `tests/unit/delivery/test_sendgrid_client.py`:

```python
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

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from regradar.delivery.sendgrid_client import send_email_alert
from regradar.models.enums import DeliveryStatus, RiskLevel


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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/delivery/ -v` (this worktree has no local `.venv` — use the shared one at the main checkout root, exactly like this)
Expected: `ModuleNotFoundError` / `ImportError` for `regradar.delivery.slack_client`, `regradar.delivery.sendgrid_client`, `regradar.delivery.types` on every test.

- [ ] **Step 4: Write the implementation**

Create `src/regradar/delivery/types.py`:

```python
"""Shared result type every delivery-channel client returns."""

from pydantic import BaseModel

from regradar.models.enums import DeliveryStatus


class DeliveryResult(BaseModel):
    status: DeliveryStatus
    response_code: int | None = None
```

Create `src/regradar/delivery/slack_client.py`:

```python
"""Sends filing alerts to Slack via an Incoming Webhook, Block Kit formatted."""

import logging

import httpx

from regradar.delivery.types import DeliveryResult
from regradar.models.enums import DeliveryStatus, RiskLevel

logger = logging.getLogger(__name__)

_RISK_COLOR: dict[RiskLevel, str] = {
    RiskLevel.LOW: "#16A34A",
    RiskLevel.MEDIUM: "#D97706",
    RiskLevel.HIGH: "#EA580C",
    RiskLevel.CRITICAL: "#DC2626",
}
_DEFAULT_COLOR = "#64748B"


async def send_slack_alert(
    webhook_url: str,
    entity_name: str,
    filing_type: str,
    filing_url: str,
    risk_level: RiskLevel | None,
    cco_summary: str,
) -> DeliveryResult:
    color = _RISK_COLOR.get(risk_level, _DEFAULT_COLOR) if risk_level else _DEFAULT_COLOR
    risk_text = risk_level.value if risk_level else "unknown"
    payload = {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*{entity_name}* — {filing_type}\n"
                                f"Risk: *{risk_text}*\n"
                                f"{cco_summary}\n"
                                f"<{filing_url}|View filing>"
                            ),
                        },
                    }
                ],
            }
        ]
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(webhook_url, json=payload)
    except httpx.RequestError as exc:
        logger.warning("Slack delivery failed (request error): %s", exc)
        return DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)

    if response.status_code == 200 and response.text.strip() == "ok":
        return DeliveryResult(status=DeliveryStatus.SENT, response_code=response.status_code)
    logger.warning("Slack delivery failed: status=%s body=%r", response.status_code, response.text)
    return DeliveryResult(status=DeliveryStatus.FAILED, response_code=response.status_code)
```

Create `src/regradar/delivery/sendgrid_client.py`:

```python
"""Sends filing alerts as HTML email via SendGrid's mail/send API."""

import logging

import httpx

from regradar.core.config import get_settings
from regradar.delivery.types import DeliveryResult
from regradar.models.enums import DeliveryStatus, RiskLevel

logger = logging.getLogger(__name__)

SENDGRID_MAIL_SEND_URL = "https://api.sendgrid.com/v3/mail/send"


def _render_html(entity_name: str, filing_type: str, risk_level: RiskLevel | None, executive_brief: str) -> str:
    risk_text = risk_level.value if risk_level else "unknown"
    return (
        f"<h2>{entity_name} — {filing_type}</h2>"
        f"<p><strong>Risk level:</strong> {risk_text}</p>"
        f"<p>{executive_brief}</p>"
    )


async def send_email_alert(
    recipient: str,
    entity_name: str,
    filing_type: str,
    risk_level: RiskLevel | None,
    executive_brief: str,
) -> DeliveryResult:
    settings = get_settings()
    if not settings.sendgrid_api_key:
        logger.warning("SendGrid not configured; skipping email delivery")
        return DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)

    html = _render_html(entity_name, filing_type, risk_level, executive_brief)
    payload = {
        "personalizations": [{"to": [{"email": recipient}]}],
        "from": {"email": settings.sendgrid_from_email},
        "subject": f"RegRadar Alert: {entity_name} — {filing_type}",
        "content": [{"type": "text/html", "value": html}],
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/delivery/ -v`
Expected: all 8 tests PASS. If `pytest.mark.asyncio` tests fail to collect/run, check `pyproject.toml`'s `[tool.pytest.ini_options]` for `asyncio_mode` — the project already has async tests elsewhere (e.g. `tests/unit/workers/test_pipeline_tasks.py`'s `async def test_mark_filing_failed_updates_status_and_error`), so this should already be configured; do not change pytest config unless a real collection error demands it.

- [ ] **Step 6: Run the full unit test suite to check for regressions**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -v`
Expected: all tests PASS.

- [ ] **Step 7: Run ruff and mypy**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check .` and `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/`
Expected: both clean. Fix any issue before committing (do not defer to a follow-up commit).

- [ ] **Step 8: Commit**

```bash
git add src/regradar/core/config.py .env.example src/regradar/delivery/types.py src/regradar/delivery/slack_client.py src/regradar/delivery/sendgrid_client.py tests/unit/delivery/test_slack_client.py tests/unit/delivery/test_sendgrid_client.py
git commit -m "Add Slack and SendGrid delivery clients (AGENT-10)"
```

---

## Task 2: Webhook dispatcher (SSRF validation + HMAC signing)

**Files:**
- Create: `src/regradar/delivery/webhook_dispatcher.py`
- Create: `tests/unit/delivery/test_webhook_dispatcher.py`

**Interfaces:**
- Consumes: `get_settings()` (`env: str`, `webhook_hmac_algorithm: str`), `DeliveryResult` from `regradar.delivery.types` (Task 1).
- Produces: `WebhookValidationError(ValueError)`, `validate_webhook_url(url: str) -> None` (raises `WebhookValidationError` on a private/loopback/link-local/reserved address or, outside `development` env, a non-https URL), `send_webhook_alert(url: str, hmac_secret: str, payload: dict) -> DeliveryResult` from `regradar.delivery.webhook_dispatcher` — consumed by Task 3's `delivery_agent.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/delivery/test_webhook_dispatcher.py`:

```python
"""Unit tests for webhook URL SSRF validation and signed dispatch — no real network calls."""

import hashlib
import hmac
import json
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

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from regradar.delivery.webhook_dispatcher import (
    WebhookValidationError,
    send_webhook_alert,
    validate_webhook_url,
)
from regradar.models.enums import DeliveryStatus


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
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 0))]):
        with pytest.raises(WebhookValidationError):
            validate_webhook_url("https://internal.example.com/webhook")


def test_validate_webhook_url_rejects_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 0))]):
        with pytest.raises(WebhookValidationError):
            validate_webhook_url("https://localhost.example.com/webhook")


def test_validate_webhook_url_rejects_link_local_metadata_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV", "production")
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("169.254.169.254", 0))]):
        with pytest.raises(WebhookValidationError):
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
    expected_signature = hmac.new(secret.encode(), sent_body, hashlib.sha256).hexdigest()
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
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 0))]):
        with pytest.raises(WebhookValidationError):
            await send_webhook_alert("https://internal.example.com/hook", "secret", {"a": 1})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/delivery/test_webhook_dispatcher.py -v`
Expected: `ModuleNotFoundError`/`ImportError` on every test.

- [ ] **Step 3: Write the implementation**

Create `src/regradar/delivery/webhook_dispatcher.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/delivery/test_webhook_dispatcher.py -v`
Expected: all 10 tests PASS.

- [ ] **Step 5: Run the full unit test suite, ruff, and mypy**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -v`, `... -m ruff check .`, `... -m mypy src/`
Expected: all clean. Fix before committing.

- [ ] **Step 6: Commit**

```bash
git add src/regradar/delivery/webhook_dispatcher.py tests/unit/delivery/test_webhook_dispatcher.py
git commit -m "Add HMAC-signed webhook dispatcher with SSRF validation (AGENT-10)"
```

---

## Task 3: The real `deliver_node` (Delivery Agent)

**Files:**
- Create: `src/regradar/agents/delivery_agent.py`
- Create: `tests/unit/agents/test_delivery_agent.py`

**Interfaces:**
- Consumes: `send_slack_alert`, `send_email_alert`, `send_webhook_alert`, `WebhookValidationError`, `DeliveryResult` (Tasks 1-2); `PipelineState` (fields: `filing_id`, `domain`, `risk_level`, `briefs: BriefSet | None`); `Delivery`, `Webhook`, `Filing` ORM models; `DeliveryChannel`, `DeliveryStatus`, `RiskLevel` enums.
- Produces: `async def deliver_node(state: PipelineState, config: RunnableConfig) -> PipelineState` — the node Task 4 wires into the graph. Returns `state.model_copy(update={"delivery_status": <comma-joined per-channel summary string>})` when it ran (even if every channel was skipped/unconfigured — the string is `"none"` in that case, never `None`), or `state` unchanged (`delivery_status` stays `None`) if `state.briefs is None` or the filing isn't found — this `None`-vs-non-`None` distinction is exactly what Task 4's `pipeline_tasks.py` change reads to decide whether to promote `filing.status` to `COMPLETE`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/agents/test_delivery_agent.py`:

```python
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


def test_deliver_node_skips_when_briefs_missing() -> None:
    state = PipelineState(filing_id=uuid.uuid4(), raw_text="")
    db = AsyncMock()

    import asyncio

    result = asyncio.get_event_loop().run_until_complete(deliver_node(state, _config(db)))

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
    assert result.delivery_status == "none"


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
```

Note the first test (`test_deliver_node_skips_when_briefs_missing`) uses `asyncio.get_event_loop().run_until_complete(...)` rather than `@pytest.mark.asyncio` — this mirrors an existing sync-context pattern already used elsewhere for calling an async function directly in a sync test in this codebase; if that idiom causes a deprecation warning or collection issue in this project's actual pytest-asyncio version, convert it to a plain `@pytest.mark.asyncio async def` test instead — the content of the assertion is what matters, not the exact async-invocation mechanism.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/agents/test_delivery_agent.py -v`
Expected: `ModuleNotFoundError`/`ImportError` for `regradar.agents.delivery_agent` on every test.

- [ ] **Step 3: Write the implementation**

Create `src/regradar/agents/delivery_agent.py`:

```python
"""The real deliver graph node — fans out to Slack, email, and every
registered, filter-matching webhook for a filing, writing a Delivery row
per attempt.

Unlike every other node in this graph, deliver_node both reads AND writes
the database mid-graph: idempotency (never re-send an already-sent
channel) and the webhook fan-out list both require a DB read before
deciding what to send, and multiple Delivery rows per filing (one per
channel/webhook) don't fit the single-row post-graph persistence pattern
used for Extraction/Brief in workers/pipeline_tasks.py. This extends
retrieve_node's existing precedent of an async node with a DB session
threaded through config={"configurable": {"db": db}} from reads to writes.

Two things this node deliberately does NOT do, and why:
- It never reads filing.domain/filing.risk_level from the DB-fetched
  Filing row — pipeline_tasks.py only sets those on the Filing object
  AFTER ainvoke() (and this node) returns, so they're stale mid-run.
  state.domain/state.risk_level (set earlier in this same graph run by
  triage_node) are the only correct source for classification-derived
  content. The Filing row is fetched only for entity_name/filing_type/
  filing_url, which PipelineState doesn't carry.
- It never sets filing.status. pipeline_tasks.py's post-graph code
  unconditionally re-decides filing.status from result["extraction"]/
  result["briefs"] after the graph completes, on the SAME ORM object
  (SQLAlchemy's identity map — db.get(Filing, id) on the same session
  returns the same Python instance) — anything this node set there would
  simply be overwritten moments later. pipeline_tasks.py alone decides
  filing.status; this node signals "delivery ran" via
  state.delivery_status being non-None, which pipeline_tasks.py reads.

No organization concept exists in the schema (SEC-05 is a separate,
deferred ticket) — Slack and email are single global destinations
(settings.slack_webhook_url / settings.delivery_email_recipient), not
per-organization. Webhook fan-out is "every active Webhook row whose
filter_domain/filter_min_risk matches this filing" — webhooks are scoped
to api_key_id, not to any tenant.
"""

import logging
from datetime import UTC, datetime

from langchain_core.runnables import RunnableConfig
from sqlalchemy import select

from regradar.agents.state import PipelineState
from regradar.core.config import get_settings
from regradar.delivery.sendgrid_client import send_email_alert
from regradar.delivery.slack_client import send_slack_alert
from regradar.delivery.types import DeliveryResult
from regradar.delivery.webhook_dispatcher import WebhookValidationError, send_webhook_alert
from regradar.models.delivery import Delivery
from regradar.models.enums import DeliveryChannel, DeliveryStatus, RiskLevel
from regradar.models.filing import Filing
from regradar.models.webhook import Webhook

logger = logging.getLogger(__name__)

_RISK_ORDER: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def _webhook_matches(webhook: Webhook, domain, risk_level: RiskLevel | None) -> bool:
    if webhook.filter_domain and domain and webhook.filter_domain != domain.value:
        return False
    if webhook.filter_min_risk and risk_level:
        if _RISK_ORDER[risk_level] < _RISK_ORDER[webhook.filter_min_risk]:
            return False
    return True


async def _record_delivery(
    db,
    filing_id,
    channel: DeliveryChannel,
    recipient: str,
    result: DeliveryResult,
    webhook_id=None,
) -> None:
    db.add(
        Delivery(
            filing_id=filing_id,
            channel=channel,
            webhook_id=webhook_id,
            recipient=recipient,
            status=result.status,
            response_code=result.response_code,
            attempt_count=1,
            sent_at=datetime.now(UTC) if result.status == DeliveryStatus.SENT else None,
        )
    )
    await db.commit()


async def deliver_node(state: PipelineState, config: RunnableConfig) -> PipelineState:
    if state.briefs is None:
        logger.warning("No briefs available for filing %s; skipping delivery", state.filing_id)
        return state

    db = config["configurable"]["db"]
    settings = get_settings()

    filing = await db.get(Filing, state.filing_id)
    if filing is None:
        logger.warning("Filing %s not found; skipping delivery", state.filing_id)
        return state

    existing = await db.execute(
        select(Delivery).where(
            Delivery.filing_id == state.filing_id, Delivery.status == DeliveryStatus.SENT
        )
    )
    already_sent = {(d.channel, d.webhook_id) for d in existing.scalars().all()}

    statuses: list[str] = []

    # --- Slack ---
    if (DeliveryChannel.SLACK, None) not in already_sent:
        if settings.slack_webhook_url:
            slack_url = settings.slack_webhook_url.get_secret_value()
            try:
                result = await send_slack_alert(
                    webhook_url=slack_url,
                    entity_name=filing.entity_name,
                    filing_type=filing.filing_type,
                    filing_url=filing.filing_url,
                    risk_level=state.risk_level,
                    cco_summary=state.briefs.cco_summary,
                )
            except Exception as exc:  # noqa: BLE001 — one channel's crash must not block the others,
                # and every attempt still gets a Delivery row per this ticket's acceptance criteria
                logger.warning("Slack delivery raised for filing %s: %s", state.filing_id, exc)
                result = DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)
            await _record_delivery(db, filing.id, DeliveryChannel.SLACK, slack_url, result)
            statuses.append(f"slack={result.status.value}")
        else:
            statuses.append("slack=not_configured")

    # --- Email ---
    if (DeliveryChannel.EMAIL, None) not in already_sent:
        if settings.sendgrid_api_key and settings.delivery_email_recipient:
            recipient = settings.delivery_email_recipient
            try:
                result = await send_email_alert(
                    recipient=recipient,
                    entity_name=filing.entity_name,
                    filing_type=filing.filing_type,
                    risk_level=state.risk_level,
                    executive_brief=state.briefs.executive_brief,
                )
            except Exception as exc:  # noqa: BLE001 — see Slack's comment above
                logger.warning("Email delivery raised for filing %s: %s", state.filing_id, exc)
                result = DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)
            await _record_delivery(db, filing.id, DeliveryChannel.EMAIL, recipient, result)
            statuses.append(f"email={result.status.value}")
        else:
            statuses.append("email=not_configured")

    # --- Webhooks ---
    webhooks_result = await db.execute(select(Webhook).where(Webhook.is_active.is_(True)))
    for webhook in webhooks_result.scalars().all():
        if (DeliveryChannel.WEBHOOK, webhook.id) in already_sent:
            continue
        if not _webhook_matches(webhook, state.domain, state.risk_level):
            continue
        payload = {
            "filing_id": str(filing.id),
            "entity_name": filing.entity_name,
            "filing_type": filing.filing_type,
            "domain": state.domain.value if state.domain else None,
            "risk_level": state.risk_level.value if state.risk_level else None,
            "executive_brief": state.briefs.executive_brief,
            "filing_url": filing.filing_url,
        }
        try:
            result = await send_webhook_alert(webhook.url, webhook.hmac_secret, payload)
        except WebhookValidationError as exc:
            logger.warning(
                "Webhook %s failed URL validation for filing %s: %s",
                webhook.id,
                state.filing_id,
                exc,
            )
            result = DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)
        except Exception as exc:  # noqa: BLE001 — see Slack's comment above
            logger.warning("Webhook delivery raised for filing %s: %s", state.filing_id, exc)
            result = DeliveryResult(status=DeliveryStatus.FAILED, response_code=None)
        await _record_delivery(
            db, filing.id, DeliveryChannel.WEBHOOK, webhook.url, result, webhook_id=webhook.id
        )
        statuses.append(f"webhook:{webhook.id}={result.status.value}")

    return state.model_copy(update={"delivery_status": ", ".join(statuses) if statuses else "none"})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/agents/test_delivery_agent.py -v`
Expected: all 10 tests PASS.

- [ ] **Step 5: Run the full unit test suite, ruff, and mypy**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -v`, `... -m ruff check .`, `... -m mypy src/`
Expected: all clean. Fix before committing.

- [ ] **Step 6: Commit**

```bash
git add src/regradar/agents/delivery_agent.py tests/unit/agents/test_delivery_agent.py
git commit -m "Add Delivery Agent with idempotent multi-channel fan-out (AGENT-10)"
```

---

## Task 4: Wire `deliver_node` into the graph and resolve the `filing.status` conflict

**Files:**
- Modify: `src/regradar/agents/graph.py`
- Modify: `tests/unit/agents/test_graph.py`
- Modify: `src/regradar/workers/pipeline_tasks.py`
- Modify: `tests/unit/workers/test_pipeline_tasks.py`

**Interfaces:**
- Consumes: `deliver_node` from `regradar.agents.delivery_agent` (Task 3).
- Produces: the compiled graph's `deliver` node is now real; `pipeline_tasks.py`'s status-decision logic gains a `FilingStatus.COMPLETE` branch, reading `result["delivery_status"]`.

- [ ] **Step 1: Update `test_graph.py`**

`tests/unit/agents/test_graph.py` currently imports `deliver_node` and includes it in `test_stub_node_returns_state_unchanged`'s `@pytest.mark.parametrize("node", [deliver_node])` list — this is the same situation AGENT-08's Task 2 resolved for `summarize_node`. Since `deliver_node` is now real (and async, unlike the stub it replaces), it must be removed from that parametrize list entirely (there'd be no remaining stub node left to test with that parametrized form, since `deliver_node` was the only entry).

Read the current full contents of `tests/unit/agents/test_graph.py` first (it may have changed shape since AGENT-08 removed `summarize_node` from this same list — confirm the current parametrize list's exact contents before editing, don't assume it still matches an old snapshot). Remove `deliver_node` from the `from regradar.agents.graph import (...)` import list and from the parametrize list. If removing `deliver_node` leaves the parametrize list empty, remove the entire `test_stub_node_returns_state_unchanged` test function and its parametrize decorator (there are no more passthrough-stub nodes left in the graph to test that way — `deliver_node` was the last one). Leave `route_after_triage`'s tests untouched.

- [ ] **Step 2: Run the test file to confirm it still passes with the stub in place**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/agents/test_graph.py -v`
Expected: all remaining tests PASS (confirms removing the stub-parametrize entry didn't break anything, before `deliver_node` itself changes underneath it).

- [ ] **Step 3: Wire the real `deliver_node` into `graph.py`**

In `src/regradar/agents/graph.py`, remove the stub:

```python
def deliver_node(state: PipelineState) -> PipelineState:
    """Stub — replaced by the Slack/email/webhook fan-out agent in AGENT-10."""
    return state
```

Add the import alongside the other real-node imports:

```python
from regradar.agents.delivery_agent import deliver_node
```

Update the module docstring's first paragraph to reflect that `deliver_node` is now real too (find the existing docstring naming `triage_node, retrieve_node, analyze_node, and summarize_node are real implementations... deliver_node is still a stub for AGENT-10` and update it to say all five are real, with no remaining stub). Leave `build_graph()`'s `graph.add_node("deliver", deliver_node)` line unchanged — it already resolves correctly to the imported real (now-async) function via normal Python name binding, exactly like AGENT-08's `summarize_node` wiring.

- [ ] **Step 4: Run tests to verify graph wiring still works**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/agents/test_graph.py tests/unit/agents/test_delivery_agent.py tests/integration/test_pipeline_graph.py -v`
Expected: all PASS. If `test_pipeline_graph.py` runs a fixture `PipelineState` through the full compiled graph and that fixture has no `db` in its `config`, `deliver_node` will hit `config["configurable"]["db"]` — check whether that integration test already passes a `db` config the same way `retrieve_node` needed one (it should, since `retrieve_node` already requires this); if the integration test's fixture `PipelineState` has no `briefs` set, `deliver_node`'s early-return guard means it never touches `db` at all, so this may be a non-issue — verify by reading the current test rather than assuming.

- [ ] **Step 5: Update `pipeline_tasks.py`'s status-decision logic**

In `src/regradar/workers/pipeline_tasks.py`, replace the status-decision block:

```python
        if result["domain"] is None:
            filing.status = FilingStatus.NEEDS_CLASSIFICATION
        else:
            filing.domain = result["domain"]
            filing.risk_level = result["risk_level"]
            filing.classification_confidence = result["classification_confidence"]
            extraction_missing = result["extraction"] is None and chunks
            briefs_missing = result["extraction"] is not None and result["briefs"] is None
            if extraction_missing or briefs_missing:
                filing.status = FilingStatus.NEEDS_REVIEW
            else:
                filing.status = FilingStatus.CLASSIFYING
        await db.commit()
```

with:

```python
        if result["domain"] is None:
            filing.status = FilingStatus.NEEDS_CLASSIFICATION
        else:
            filing.domain = result["domain"]
            filing.risk_level = result["risk_level"]
            filing.classification_confidence = result["classification_confidence"]
            extraction_missing = result["extraction"] is None and chunks
            briefs_missing = result["extraction"] is not None and result["briefs"] is None
            if extraction_missing or briefs_missing:
                filing.status = FilingStatus.NEEDS_REVIEW
            elif result["delivery_status"] is not None:
                filing.status = FilingStatus.COMPLETE
            else:
                filing.status = FilingStatus.CLASSIFYING
        await db.commit()
```

No other changes to this file are needed — `deliver_node` (Task 3) already handles its own `Delivery` row persistence entirely inline within the graph run.

- [ ] **Step 6: Update `test_pipeline_tasks.py`'s mocked `ainvoke()` return dicts**

Every mocked `ainvoke()` return dict in `tests/unit/workers/test_pipeline_tasks.py` currently has `"extraction": ...` and (after AGENT-08) `"briefs": ...` keys but no `"delivery_status"` key — since the code now reads `result["delivery_status"]` unconditionally in the `elif` branch, every one of them needs a `"delivery_status"` key added, or the test raises `KeyError`. **Do not guess the count** — AGENT-08's Task 3 and AGENT-09's Task 3 both undercounted how many pre-existing mock dicts needed a new key (found 6 vs 7, and similar). Instead: search the current file for every `return_value={` / `"extraction":` occurrence yourself and add `"delivery_status": None` (or a real string, for the two tests below) to each one found — verify by grepping `return_value=\{` and `"extraction":` in the file before editing, and again after, to confirm the count of edited dicts matches the count found.

For tests that should exercise the new `COMPLETE` branch specifically, add `"delivery_status": "slack=sent"` (or similar non-`None` string) instead of `None` in that one test's mock dict, and add an assertion `assert filing.status == FilingStatus.COMPLETE`. A natural candidate is extending `test_process_filing_persists_briefs_on_success` (or adding a new test alongside it) — pick whichever keeps the change minimal and clearly scoped; if extending an existing test, rename it if its name would become misleading (e.g. to `test_process_filing_marks_complete_when_delivery_ran`), or add a new test alongside the existing one instead of overloading it, at your discretion given what reads more clearly once written.

Add one more test confirming the *no-delivery* case still works: a mock `ainvoke()` return dict with `"delivery_status": None` alongside successful `"extraction"`/`"briefs"` results asserts `filing.status == FilingStatus.CLASSIFYING` (preserving the pre-AGENT-10 default behavior when delivery didn't run, e.g. because `state.briefs` was `None` for some other reason).

- [ ] **Step 7: Run tests to verify they pass**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/workers/test_pipeline_tasks.py -v`
Expected: all tests PASS.

- [ ] **Step 8: Run the full unit test suite, ruff, and mypy**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -v`, `... -m ruff check .`, `... -m mypy src/`
Expected: all clean.

- [ ] **Step 9: Commit**

```bash
git add src/regradar/agents/graph.py tests/unit/agents/test_graph.py src/regradar/workers/pipeline_tasks.py tests/unit/workers/test_pipeline_tasks.py
git commit -m "Wire real deliver_node into the graph; mark filings complete after delivery (AGENT-10)"
```

---

## Task 5: Live verification against real Slack, SendGrid, and a real webhook target

Per this project's established live-verification policy, run the full delivery path against real services before considering AGENT-10 done — not just mocked unit tests. **This task requires real credentials in the user's local `.env` that they must set up themselves** (a real Slack Incoming Webhook URL and a real SendGrid account/API key/verified sender — see the earlier conversation with the user for exact setup steps; never ask the user to paste credentials into chat, and never write them yourself — confirm they're present in `.env` via `grep`, don't print values).

**Files:** none (manual verification, no code changes).

- [ ] **Step 1: Confirm real credentials are present**

Check (without printing values): `grep -q "^SLACK_WEBHOOK_URL=." .env && echo present`, `grep -q "^SENDGRID_API_KEY=." .env && echo present`, `grep -q "^SENDGRID_FROM_EMAIL=." .env && echo present`, `grep -q "^DELIVERY_EMAIL_RECIPIENT=." .env && echo present`. If any are missing, stop and ask the user to add them — do not proceed with a partial/fake verification and claim it's complete.

- [ ] **Step 2: Get a real webhook test destination**

No account needed — generate a temporary unique URL at `https://webhook.site` (or ask the user if they'd rather point at something they control) and note the URL for use in Step 4.

- [ ] **Step 3: Insert a real test `Webhook` row**

Using a real Postgres connection (start `infra-postgres-1` per the pattern used in AGENT-08's live verification — `docker start infra-postgres-1`), insert one real `ApiKey` row (any placeholder `key_hash`/`owner_label` — this table has no real registration flow yet, matching how test filings were manually inserted in prior tickets) and one real `Webhook` row referencing it, with `url` set to the webhook.site URL from Step 2, a real random `hmac_secret`, and `is_active=True`.

- [ ] **Step 4: Run a real end-to-end delivery call**

Write a short throwaway script (not committed) that constructs a `PipelineState` with a real `filing_id` (referencing a real, freshly-inserted test `Filing` row — mirror AGENT-08's `Filing` insertion pattern), `domain`, `risk_level`, and a populated `briefs: BriefSet`, then calls `deliver_node(state, {"configurable": {"db": db}})` directly against the real Postgres session. Confirm:
  - A real Slack message actually lands in the configured Slack channel — check visually.
  - A real email actually lands in the recipient's inbox — check visually.
  - The webhook.site page shows a received POST with the correct `X-RegRadar-Signature` header; manually recompute the HMAC-SHA256 of the received raw body using the test webhook's `hmac_secret` and confirm it matches the header — this is the actual acceptance-criteria check, not just "a POST arrived."
  - Three real `deliveries` rows exist in Postgres, `channel`/`status`/`response_code` all correct.
  - Re-running `deliver_node` with the same `filing_id` a second time sends nothing again (idempotency) — confirm no new Slack message/email/webhook POST arrives, and no new `deliveries` rows are created (still exactly 3).

- [ ] **Step 5: Clean up**

Delete the test `Delivery`, `Webhook`, `ApiKey`, and `Filing` rows. Stop `infra-postgres-1` (`docker stop infra-postgres-1`). Remove the throwaway script. Remove the copied `.env` from the worktree if one was copied in for this verification (matching prior tickets' cleanup pattern).

- [ ] **Step 6: Update project memory**

Record the outcome of live verification — this happens outside the plan file, as a memory update once the ticket is complete.

---

## Self-Review Notes

- **Spec coverage:** All 4 AGENT-10 acceptance criteria covered — (1) fires exactly one Slack/email/webhook-per-active-webhook attempt per filing (Task 3's per-channel-once logic, each guarded by the idempotency check); (2) every attempt produces a `Delivery` row with correct channel/status/response_code, even on an unexpected exception (Task 3's `_record_delivery` called unconditionally per attempted channel); (3) idempotency via the `already_sent` set built from `status == SENT` rows, keyed on `(channel, webhook_id)` (Task 3); (4) HMAC signing uses each webhook's own `hmac_secret`, never a shared secret (Task 2's `send_webhook_alert(url, hmac_secret, payload)` takes the secret as a per-call argument, sourced from `webhook.hmac_secret` in Task 3's loop, never from a global config value). DELIV-01/DELIV-02's detailed client specs are folded into Tasks 1-2 as this ticket's own AI Coding Prompt calls for. SEC-02's dispatch-time validator is implemented in minimal form (Task 2); SEC-03's replay protection and full API-08 webhook registration are explicitly out of scope, documented as such.
- **Placeholder scan:** No TBD/TODO markers; all code blocks are complete and copy-pasteable. Task 4's Steps 1 and 6 intentionally instruct the implementer to verify exact counts/current file contents rather than assuming a stale snapshot — this is deliberate process guidance (informed by two prior tickets' undercounting mistakes), not a placeholder.
- **Type consistency:** `DeliveryResult` (`status`, `response_code`) used identically across Task 1 (definition + Slack/SendGrid clients), Task 2 (webhook dispatcher), and Task 3 (`delivery_agent.py`'s `_record_delivery` helper). `send_slack_alert`/`send_email_alert`/`send_webhook_alert` signatures match between their Task 1/2 definitions and Task 3's call sites exactly (keyword arguments used consistently). `state.domain`/`state.risk_level` vs. `filing.entity_name`/`filing.filing_type`/`filing.filing_url` source split is applied consistently across the Slack, email, and webhook branches in Task 3 — no branch accidentally reads a stale `filing.risk_level`/`filing.domain`.
