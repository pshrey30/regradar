"""Tests for the WebSocket at /v1/filings/{id}/status/ws."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.routers import filings as filings_module
from regradar.models.enums import ApiKeyRole, FilingStatus


def _authenticated_key_row(role: ApiKeyRole = ApiKeyRole.ADMIN):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.organization_id = uuid.uuid4()
    row.role = role
    row.owner_label = "test-owner"
    row.rate_limit_per_minute = 1000
    row.is_active = True
    row.email = None
    row.password_hash = None
    return row


def _mock_auth(monkeypatch: pytest.MonkeyPatch, *, role: ApiKeyRole = ApiKeyRole.ADMIN):
    """get_current_key (used directly by the websocket route, not via
    enforce_rate_limit) does its own lookup on a fresh session — mock that
    same session factory `deps.get_current_key` reads from."""
    row = _authenticated_key_row(role)

    mock_auth_db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)
    mock_auth_db.execute = AsyncMock(return_value=result)
    mock_auth_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_auth_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(deps_module, "get_session_factory", lambda: mock_session_factory)

    return row


def _mock_filing_lookup(monkeypatch: pytest.MonkeyPatch, *, filing):
    """The websocket route calls `get_session_factory()` via its own
    `from regradar.core.db import get_session_factory` binding in
    filings.py's namespace — patch that reference, not core.db's own
    (which is a separate name and wouldn't affect this route's calls)."""
    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.execute = AsyncMock(side_effect=[MagicMock(), MagicMock(), MagicMock()])

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(filings_module, "get_session_factory", lambda: mock_session_factory)


def _mock_listen(monkeypatch: pytest.MonkeyPatch, *, notifications: list[dict]):
    """Fakes core.pg_listen.listen: yields a queue pre-loaded with the
    given notification payloads, so the route's LISTEN loop sees them
    without a real Postgres connection."""

    @asynccontextmanager
    async def _fake_listen(_channel: str):
        queue: asyncio.Queue[str] = asyncio.Queue()
        for notification in notifications:
            queue.put_nowait(json.dumps(notification))
        yield queue

    monkeypatch.setattr(filings_module, "listen", _fake_listen)


def _filing_row(*, filing_id: uuid.UUID, status: FilingStatus = FilingStatus.ANALYZING):
    filing = MagicMock()
    filing.id = filing_id
    filing.status = status
    return filing


def test_status_ws_closes_4404_for_unknown_filing(monkeypatch: pytest.MonkeyPatch):
    _mock_auth(monkeypatch)
    _mock_filing_lookup(monkeypatch, filing=None)

    client = TestClient(create_app())
    with (
        pytest.raises(Exception),  # noqa: B017 - starlette raises WebSocketDisconnect(code=4404)
        client.websocket_connect(f"/v1/filings/{uuid.uuid4()}/status/ws"),
    ):
        pass


def test_status_ws_sends_current_status_immediately_on_connect(monkeypatch: pytest.MonkeyPatch):
    filing_id = uuid.uuid4()
    _mock_auth(monkeypatch)
    _mock_filing_lookup(monkeypatch, filing=_filing_row(filing_id=filing_id, status=FilingStatus.ANALYZING))
    _mock_listen(monkeypatch, notifications=[])

    client = TestClient(create_app())
    with client.websocket_connect(
        f"/v1/filings/{filing_id}/status/ws", headers={"Authorization": "Bearer rr_test-key"}
    ) as ws:
        assert ws.receive_json() == {"status": "analyzing"}


def test_status_ws_forwards_matching_notification(monkeypatch: pytest.MonkeyPatch):
    filing_id = uuid.uuid4()
    other_id = uuid.uuid4()
    _mock_auth(monkeypatch)
    _mock_filing_lookup(monkeypatch, filing=_filing_row(filing_id=filing_id, status=FilingStatus.ANALYZING))
    _mock_listen(
        monkeypatch,
        notifications=[
            {"filing_id": str(other_id), "status": "complete"},  # a different filing — ignored
            {"filing_id": str(filing_id), "status": "complete"},
        ],
    )

    client = TestClient(create_app())
    with client.websocket_connect(
        f"/v1/filings/{filing_id}/status/ws", headers={"Authorization": "Bearer rr_test-key"}
    ) as ws:
        assert ws.receive_json() == {"status": "analyzing"}  # initial snapshot
        assert ws.receive_json() == {"status": "complete"}  # the matching notification
