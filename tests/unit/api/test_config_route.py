"""Tests for POST /v1/config/sources."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.models.enums import ApiKeyRole, FilingSource
from regradar.models.source_config import SourceConfig


def _authenticated_key_row(role: ApiKeyRole):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.organization_id = uuid.uuid4()
    row.role = role
    row.owner_label = "test-owner"
    row.rate_limit_per_minute = 1000
    row.is_active = True
    return row


def _mock_auth_and_rate_limit(monkeypatch: pytest.MonkeyPatch, *, role: ApiKeyRole):
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

    mock_redis = MagicMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock()
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: mock_redis)


def _source_config_row(source: FilingSource, *, is_active: bool = True):
    row = MagicMock(spec=SourceConfig)
    row.source = source
    row.domains = ["financial"]
    row.is_active = is_active
    row.poll_interval_seconds = 300
    row.last_polled_at = None
    return row


def _mock_config_db(monkeypatch: pytest.MonkeyPatch, *, existing_rows: list):
    mock_db = AsyncMock()
    result = MagicMock()
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=existing_rows)))
    mock_db.execute = AsyncMock(return_value=result)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)
    return mock_db


@pytest.mark.parametrize(
    "role", [ApiKeyRole.ANALYST, ApiKeyRole.EXECUTIVE, ApiKeyRole.LEGAL_COUNSEL, ApiKeyRole.ENG_LEAD]
)
def test_update_source_config_returns_403_for_non_admin_roles(
    monkeypatch: pytest.MonkeyPatch, role: ApiKeyRole
):
    _mock_auth_and_rate_limit(monkeypatch, role=role)
    _mock_config_db(monkeypatch, existing_rows=[])

    response = TestClient(create_app()).post(
        "/v1/config/sources",
        json={"sources": ["SEC"], "domains": ["financial"]},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_update_source_config_returns_422_for_invalid_source(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_config_db(monkeypatch, existing_rows=[])

    response = TestClient(create_app()).post(
        "/v1/config/sources",
        json={"sources": ["SEC", "NOTAREALSOURCE"], "domains": []},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_sources"
    assert "NOTAREALSOURCE" in body["error"]["message"]


def test_update_source_config_activates_included_and_deactivates_omitted(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    existing_fda_row = _source_config_row(FilingSource.FDA, is_active=True)
    mock_db = _mock_config_db(monkeypatch, existing_rows=[existing_fda_row])

    response = TestClient(create_app()).post(
        "/v1/config/sources",
        json={"sources": ["SEC"], "domains": ["financial"]},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    body = response.json()
    by_source = {row["source"]: row for row in body}
    assert by_source["SEC"]["is_active"] is True
    assert by_source["SEC"]["domains"] == ["financial"]
    assert by_source["FDA"]["is_active"] is False
    assert existing_fda_row.is_active is False
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


def test_update_source_config_with_no_prior_rows_and_empty_sources_deactivates_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_config_db(monkeypatch, existing_rows=[])

    response = TestClient(create_app()).post(
        "/v1/config/sources",
        json={"sources": [], "domains": []},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    assert response.json() == []
    mock_db.add.assert_not_called()
