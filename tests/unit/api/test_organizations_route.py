"""Tests for GET/PUT /v1/organizations/me/profile."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.api.routers import organizations as organizations_module
from regradar.models.enums import ApiKeyRole
from regradar.models.organization_profile import OrganizationProfile


def _authenticated_key_row(role: ApiKeyRole, organization_id: uuid.UUID):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.organization_id = organization_id
    row.role = role
    row.owner_label = "Test User"
    row.rate_limit_per_minute = 1000
    row.is_active = True
    row.email = "test@example.com"
    row.password_hash = None
    return row


def _mock_auth_and_rate_limit(monkeypatch: pytest.MonkeyPatch, *, role: ApiKeyRole, organization_id: uuid.UUID):
    row = _authenticated_key_row(role, organization_id)

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

    return row


def _mock_route_db(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)

    return mock_db


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_get_profile_returns_empty_fields_when_no_row_exists(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST, organization_id=org_id)
    mock_db = _mock_route_db(monkeypatch)
    mock_db.get = AsyncMock(return_value=None)

    response = client.get("/v1/organizations/me/profile", headers={"Authorization": "Bearer test"})

    assert response.status_code == 200
    body = response.json()
    assert body["industry"] is None
    assert body["watchlist_entities"] == []
    assert body["is_complete"] is False


def test_put_profile_rejects_non_admin(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST, organization_id=org_id)
    _mock_route_db(monkeypatch)

    response = client.put(
        "/v1/organizations/me/profile",
        headers={"Authorization": "Bearer test"},
        json={
            "industry": "Biotech",
            "business_description": "We make devices.",
            "watchlist_entities": ["Acme"],
            "products": ["Widget"],
            "risk_priorities": ["Privacy"],
        },
    )

    assert response.status_code == 403


def test_put_profile_rejects_missing_field(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN, organization_id=org_id)
    _mock_route_db(monkeypatch)

    response = client.put(
        "/v1/organizations/me/profile",
        headers={"Authorization": "Bearer test"},
        json={
            "industry": "Biotech",
            "business_description": "We make devices.",
            "watchlist_entities": [],
            "products": ["Widget"],
            "risk_priorities": ["Privacy"],
        },
    )

    assert response.status_code == 422


def test_put_profile_rejects_whitespace_only_list_entries(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN, organization_id=org_id)
    _mock_route_db(monkeypatch)

    response = client.put(
        "/v1/organizations/me/profile",
        headers={"Authorization": "Bearer test"},
        json={
            "industry": "Biotech",
            "business_description": "We make devices.",
            "watchlist_entities": ["   "],
            "products": ["Widget"],
            "risk_priorities": ["Privacy"],
        },
    )

    assert response.status_code == 422


def test_put_profile_rejects_whitespace_only_scalar_fields(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN, organization_id=org_id)
    _mock_route_db(monkeypatch)

    response = client.put(
        "/v1/organizations/me/profile",
        headers={"Authorization": "Bearer test"},
        json={
            "industry": "   ",
            "business_description": "We make devices.",
            "watchlist_entities": ["Acme"],
            "products": ["Widget"],
            "risk_priorities": ["Privacy"],
        },
    )

    assert response.status_code == 422


def test_put_profile_strips_whitespace_from_list_entries(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN, organization_id=org_id)
    mock_db = _mock_route_db(monkeypatch)
    mock_db.get = AsyncMock(return_value=None)
    mock_db.add = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()

    response = client.put(
        "/v1/organizations/me/profile",
        headers={"Authorization": "Bearer test"},
        json={
            "industry": "  Biotech  ",
            "business_description": "We make devices.",
            "watchlist_entities": ["  Acme  ", "", "   ", "Globex"],
            "products": ["Widget"],
            "risk_priorities": ["Privacy"],
        },
    )

    assert response.status_code == 200
    added = mock_db.add.call_args_list[0].args[0]
    assert added.industry == "Biotech"
    assert added.watchlist_entities == ["Acme", "Globex"]


def test_put_profile_saves_and_requeues_parked_filings(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN, organization_id=org_id)
    mock_db = _mock_route_db(monkeypatch)
    mock_db.get = AsyncMock(return_value=None)
    mock_db.add = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()

    response = client.put(
        "/v1/organizations/me/profile",
        headers={"Authorization": "Bearer test"},
        json={
            "industry": "Biotech",
            "business_description": "We make devices.",
            "watchlist_entities": ["Acme"],
            "products": ["Widget"],
            "risk_priorities": ["Privacy"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_complete"] is True
    added = mock_db.add.call_args_list[0].args[0]
    assert isinstance(added, OrganizationProfile)
    assert added.industry == "Biotech"
    mock_db.execute.assert_awaited()  # the requeue UPDATE
    mock_db.commit.assert_awaited()


def test_put_profile_reasserts_service_role_before_requeue(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """filings_write (migration 0009) is service-role-only, so the requeue
    UPDATE must run with app.current_role re-set to 'service' on this same
    session — otherwise it silently affects zero rows under RLS (the bug
    this route was fixed for)."""
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN, organization_id=org_id)
    mock_db = _mock_route_db(monkeypatch)
    mock_db.get = AsyncMock(return_value=None)
    mock_db.add = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()

    mock_set_rls_context = AsyncMock()
    monkeypatch.setattr(organizations_module, "set_rls_context", mock_set_rls_context)

    response = client.put(
        "/v1/organizations/me/profile",
        headers={"Authorization": "Bearer test"},
        json={
            "industry": "Biotech",
            "business_description": "We make devices.",
            "watchlist_entities": ["Acme"],
            "products": ["Widget"],
            "risk_priorities": ["Privacy"],
        },
    )

    assert response.status_code == 200
    mock_set_rls_context.assert_awaited_once_with(mock_db, role="service")
