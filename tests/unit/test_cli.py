"""Tests for the create-api-key CLI command."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from regradar import cli as cli_module
from regradar.models.enums import ApiKeyRole


def _patch_db(monkeypatch: pytest.MonkeyPatch):
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

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


def test_create_api_key_without_rate_limit_uses_model_default(monkeypatch):
    mock_db = _patch_db(monkeypatch)

    cli_module._create_api_key(owner_label="test-owner", role="admin")

    inserted = mock_db.add.call_args[0][0]
    # No explicit value was passed to ApiKey(...), so the ORM/DB default (60,
    # per FOUND-02) applies rather than the CLI overriding it.
    assert inserted.rate_limit_per_minute == 60
