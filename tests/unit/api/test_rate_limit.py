"""Tests for the Redis-backed rate limiting dependency."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from regradar.api.deps import AuthenticatedKey
from regradar.api.errors import ApiError
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.models.enums import ApiKeyRole


def _make_key(rate_limit_per_minute: int = 5) -> AuthenticatedKey:
    return AuthenticatedKey(
        id=uuid4(),
        role=ApiKeyRole.ANALYST,
        owner_label="test-owner",
        rate_limit_per_minute=rate_limit_per_minute,
    )


def _patch_redis(monkeypatch: pytest.MonkeyPatch, *, incr_side_effect, expire_side_effect=None):
    mock_client = MagicMock()
    mock_client.incr = AsyncMock(side_effect=incr_side_effect)
    mock_client.expire = AsyncMock(side_effect=expire_side_effect)
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: mock_client)
    return mock_client


async def test_under_limit_passes_through(monkeypatch: pytest.MonkeyPatch):
    _patch_redis(monkeypatch, incr_side_effect=[1])
    key = _make_key(rate_limit_per_minute=5)

    result = await rate_limit_module.enforce_rate_limit(key=key)

    assert result is key


async def test_first_hit_in_window_sets_expiry(monkeypatch: pytest.MonkeyPatch):
    mock_client = _patch_redis(monkeypatch, incr_side_effect=[1])
    key = _make_key(rate_limit_per_minute=5)

    await rate_limit_module.enforce_rate_limit(key=key)

    mock_client.expire.assert_awaited_once()


async def test_subsequent_hit_calls_expire_with_nx_to_avoid_resetting_ttl(
    monkeypatch: pytest.MonkeyPatch,
):
    mock_client = _patch_redis(monkeypatch, incr_side_effect=[2])
    key = _make_key(rate_limit_per_minute=5)

    await rate_limit_module.enforce_rate_limit(key=key)

    mock_client.expire.assert_awaited_once_with(
        mock_client.expire.call_args[0][0], rate_limit_module._KEY_TTL_SECONDS, nx=True
    )


async def test_expire_failure_after_successful_incr_fails_open(monkeypatch: pytest.MonkeyPatch):
    _patch_redis(
        monkeypatch, incr_side_effect=[1], expire_side_effect=ConnectionError("redis down")
    )
    key = _make_key(rate_limit_per_minute=5)

    result = await rate_limit_module.enforce_rate_limit(key=key)

    assert result is key


async def test_over_limit_raises_429_with_retry_after(monkeypatch: pytest.MonkeyPatch):
    _patch_redis(monkeypatch, incr_side_effect=[6])
    key = _make_key(rate_limit_per_minute=5)

    with pytest.raises(ApiError) as exc_info:
        await rate_limit_module.enforce_rate_limit(key=key)

    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "rate_limit_exceeded"
    assert exc_info.value.headers is not None
    assert "Retry-After" in exc_info.value.headers
    retry_after = int(exc_info.value.headers["Retry-After"])
    assert 1 <= retry_after <= 60


async def test_two_keys_have_independent_counters(monkeypatch: pytest.MonkeyPatch):
    mock_client = _patch_redis(monkeypatch, incr_side_effect=[6, 1])
    key_a = _make_key(rate_limit_per_minute=5)
    key_b = _make_key(rate_limit_per_minute=5)

    with pytest.raises(ApiError):
        await rate_limit_module.enforce_rate_limit(key=key_a)

    result_b = await rate_limit_module.enforce_rate_limit(key=key_b)

    assert result_b is key_b
    assert mock_client.incr.call_count == 2
    first_call_redis_key = mock_client.incr.call_args_list[0][0][0]
    second_call_redis_key = mock_client.incr.call_args_list[1][0][0]
    assert first_call_redis_key != second_call_redis_key
    assert str(key_a.id) in first_call_redis_key
    assert str(key_b.id) in second_call_redis_key


async def test_redis_error_fails_open(monkeypatch: pytest.MonkeyPatch):
    _patch_redis(monkeypatch, incr_side_effect=ConnectionError("redis down"))
    key = _make_key(rate_limit_per_minute=5)

    result = await rate_limit_module.enforce_rate_limit(key=key)

    assert result is key
