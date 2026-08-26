"""Tests for the create-api-key CLI command."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from regradar import cli as cli_module
from regradar.models.enums import ApiKeyRole

_DEFAULT_ORG_ID = uuid.uuid4()


def _patch_db(monkeypatch: pytest.MonkeyPatch):
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    # SEC-05: _create_api_key looks up the single default organization's id
    # via db.execute(select(...)).scalar_one() before inserting.
    org_lookup_result = MagicMock()
    org_lookup_result.scalar_one = MagicMock(return_value=_DEFAULT_ORG_ID)
    mock_db.execute = AsyncMock(return_value=org_lookup_result)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(cli_module, "get_session_factory", lambda: mock_session_factory)
    return mock_db


def test_create_api_key_inserts_row_with_correct_role(monkeypatch, capsys):
    mock_db = _patch_db(monkeypatch)

    cli_module._create_api_key(owner_label="test-owner", role="admin")

    mock_db.add.assert_called_once()
    inserted = mock_db.add.call_args[0][0]
    assert inserted.owner_label == "test-owner"
    assert inserted.role == ApiKeyRole.ADMIN
    assert inserted.is_active is True
    assert inserted.organization_id == _DEFAULT_ORG_ID
    mock_db.commit.assert_awaited_once()


def test_create_api_key_prints_plaintext_key_once(monkeypatch, capsys):
    _patch_db(monkeypatch)

    cli_module._create_api_key(owner_label="test-owner", role="analyst")

    captured = capsys.readouterr()
    assert "rr_" in captured.out


def test_create_api_key_rejects_invalid_role(monkeypatch):
    _patch_db(monkeypatch)

    with pytest.raises(SystemExit):
        cli_module._create_api_key(owner_label="test-owner", role="not-a-real-role")


def test_create_api_key_with_explicit_rate_limit(monkeypatch):
    mock_db = _patch_db(monkeypatch)

    cli_module._create_api_key(owner_label="test-owner", role="admin", rate_limit_per_minute=3)

    inserted = mock_db.add.call_args[0][0]
    assert inserted.rate_limit_per_minute == 3


def test_create_api_key_without_rate_limit_uses_cli_default(monkeypatch):
    mock_db = _patch_db(monkeypatch)

    cli_module._create_api_key(owner_label="test-owner", role="admin")

    inserted = mock_db.add.call_args[0][0]
    # _create_api_key always passes an explicit value to ApiKey(...) — this
    # is cli.py's own _DEFAULT_RATE_LIMIT_PER_MINUTE constant (60), not an
    # ORM/DB-level default (SQLAlchemy's mapped_column(default=60) only
    # applies at flush time, which never happens in this mocked test).
    assert inserted.rate_limit_per_minute == 60
