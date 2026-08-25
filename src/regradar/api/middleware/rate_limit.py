"""Redis-backed per-key rate limiting, applied after authentication.

enforce_rate_limit is meant to be used everywhere a route would otherwise
use get_current_key directly — it depends on get_current_key itself and
passes the resolved key straight through, so a route only ever needs one
Depends(...) call to get both authentication and rate limiting.
"""

import logging
from datetime import UTC, datetime

from fastapi import Depends

from regradar.api.deps import AuthenticatedKey, get_current_key
from regradar.api.errors import ApiError
from regradar.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60
_KEY_TTL_SECONDS = 70  # window + a safety margin against clock/scheduling jitter


async def enforce_rate_limit(
    key: AuthenticatedKey = Depends(get_current_key),
) -> AuthenticatedKey:
    """Enforce key.rate_limit_per_minute using a Redis fixed-window counter.

    Fails open on any Redis error: an outage degrades to "no rate limiting
    this request" rather than blocking all authenticated traffic.
    """
    now = datetime.now(UTC)
    window = now.strftime("%Y%m%d%H%M")
    redis_key = f"ratelimit:{key.id}:{window}"

    client = get_redis_client()
    try:
        count = await client.incr(redis_key)
        if count == 1:
            await client.expire(redis_key, _KEY_TTL_SECONDS)
    except Exception:  # noqa: BLE001 — a Redis outage must degrade, not block all traffic
        logger.warning("Rate limit check failed; failing open.", exc_info=True)
        return key

    if count > key.rate_limit_per_minute:
        seconds_until_reset = _WINDOW_SECONDS - now.second
        raise ApiError(
            status_code=429,
            code="rate_limit_exceeded",
            message="Rate limit exceeded for this API key.",
            headers={"Retry-After": str(seconds_until_reset)},
        )

    return key
