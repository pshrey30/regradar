"""Smoke tests confirming get_current_key is actually wired to a real route.

The exhaustive missing/malformed/unknown/revoked/valid-key behavior is
already covered at the dependency level in test_deps.py — this file only
confirms the wiring itself works end-to-end over real HTTP.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.models.enums import ApiKeyRole


def test_whoami_without_header_returns_401():
    response = TestClient(create_app()).get("/v1/_whoami")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_whoami_with_valid_key_returns_role_and_owner(monkeypatch: pytest.MonkeyPatch):
    row = MagicMock()
    row.id = uuid4()
    row.role = ApiKeyRole.ENG_LEAD
    row.owner_label = "test-integrator"
    row.rate_limit_per_minute = 60
    row.is_active = True

    mock_db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)
    mock_db.execute = AsyncMock(return_value=result)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(deps_module, "get_session_factory", lambda: mock_session_factory)

    response = TestClient(create_app()).get(
        "/v1/_whoami", headers={"Authorization": "Bearer rr_valid-test-key"}
    )

    assert response.status_code == 200
    assert response.json() == {"role": "eng_lead", "owner_label": "test-integrator"}
