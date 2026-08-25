"""Tests for the shared async Redis client singleton."""

from unittest.mock import MagicMock

import regradar.core.redis_client as redis_client_module


def _patch_settings(monkeypatch):
    mock_settings = MagicMock()
    mock_settings.redis_url.get_secret_value.return_value = "redis://localhost:6379/0"
    monkeypatch.setattr(redis_client_module, "get_settings", lambda: mock_settings)


def test_get_redis_client_returns_same_instance(monkeypatch):
    _patch_settings(monkeypatch)

    first = redis_client_module.get_redis_client()
    second = redis_client_module.get_redis_client()

    assert first is second


def test_get_redis_client_uses_settings_redis_url(monkeypatch):
    mock_settings = MagicMock()
    mock_settings.redis_url.get_secret_value.return_value = "redis://example-host:6379/0"
    monkeypatch.setattr(redis_client_module, "get_settings", lambda: mock_settings)

    client = redis_client_module.get_redis_client()

    # redis.asyncio.Redis stores its connection pool's connection kwargs;
    # the host it was built from should reflect the URL we passed in.
    assert client.connection_pool.connection_kwargs["host"] == "example-host"
