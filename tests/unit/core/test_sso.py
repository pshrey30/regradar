"""Unit tests for FE-02's Google OIDC protocol calls — no real network calls."""

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

from regradar.core import sso as sso_module
from regradar.core.sso import SsoError, build_google_authorize_url, generate_state_token


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from regradar.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_generate_state_token_is_unique_and_unguessable() -> None:
    a, b = generate_state_token(), generate_state_token()
    assert a != b
    assert len(a) >= 32


def test_build_google_authorize_url_includes_configured_client_and_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "real-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/v1/auth/google/callback")

    url = build_google_authorize_url(state="my-state-token")

    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=real-client-id" in url
    assert "state=my-state-token" in url
    assert "scope=openid" in url


@pytest.mark.asyncio
async def test_exchange_code_for_identity_raises_without_configured_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

    with pytest.raises(SsoError, match="not configured"):
        await sso_module.exchange_code_for_identity(code="abc")


@pytest.mark.asyncio
async def test_exchange_code_for_identity_raises_on_token_request_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        pytest.raises(SsoError, match="token exchange failed"),
    ):
        await sso_module.exchange_code_for_identity(code="abc")


@pytest.mark.asyncio
async def test_exchange_code_for_identity_raises_when_id_token_verification_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"id_token": "forged-jwt"})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(
            sso_module.google_id_token,
            "verify_oauth2_token",
            side_effect=ValueError("invalid signature"),
        ),
        pytest.raises(SsoError, match="verification failed"),
    ):
        await sso_module.exchange_code_for_identity(code="abc")


@pytest.mark.asyncio
async def test_exchange_code_for_identity_returns_identity_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"id_token": "real-jwt"})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    claims = {
        "sub": "google-sub-abc",
        "email": "person@example.com",
        "email_verified": True,
        "name": "A Person",
    }

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(sso_module.google_id_token, "verify_oauth2_token", return_value=claims),
    ):
        identity = await sso_module.exchange_code_for_identity(code="abc")

    assert identity.subject_id == "google-sub-abc"
    assert identity.email == "person@example.com"
    assert identity.email_verified is True
    assert identity.name == "A Person"
