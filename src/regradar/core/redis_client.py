"""Shared async Redis client singleton, mirroring core.db's engine-singleton pattern."""

import redis.asyncio as aioredis

from regradar.core.config import get_settings

_client: aioredis.Redis | None = None


def get_redis_client() -> aioredis.Redis:
    """Return a lazily-initialized, process-wide async Redis client."""
    global _client
    if _client is None:
        _client = aioredis.from_url(get_settings().redis_url.get_secret_value())
    return _client
