# API-03 — Rate Limiting Middleware — Design

## Context

API-02 built `get_current_key`, resolving each request to an `AuthenticatedKey` (with its own
`rate_limit_per_minute`) but not yet enforcing that limit. API-03 closes that gap: a Redis-backed
per-key rate limiter, applied to every authenticated route.

As with API-02, no real business route exists yet (API-04 onward aren't built) — the only live
route is API-02's throwaway `GET /v1/_whoami`. This ticket reuses and extends that same route to
stay live-testable, rather than inventing a second throwaway endpoint.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Algorithm | Fixed-window counter (`INCR` + TTL, keyed by `api_key_id` + current minute) | Matches the ticket's own literal AI Coding Prompt wording exactly ("increment a per-key counter in Redis keyed by api_key_id and the current minute window"). Simplest to implement, test, and reason about — the boundary-burst edge case (up to ~2x the limit right at a minute rollover) is an accepted, documented trade-off for this project's scope, not hidden |
| Composition with auth | One combined dependency, `enforce_rate_limit(key: AuthenticatedKey = Depends(get_current_key)) -> AuthenticatedKey` | Every future authenticated route uses `Depends(enforce_rate_limit)` in the exact spot it would have used `Depends(get_current_key)` — rate limiting is structurally impossible to forget, since there's only one dependency call site, not two to remember to add together |
| Redis unavailable during check | Fail open — log a warning, let the request through | Consistent with this project's existing degrade-gracefully pattern (health check, HF/OpenAI fallbacks, empty-corpus retrieval). Confirmed with the user: acceptable to let this fail open now and revisit if it becomes a real problem |
| Live-test surface | Reuse and extend API-02's `GET /v1/_whoami` (swap its dependency from `get_current_key` to `enforce_rate_limit`) | No real route exists to test against yet; inventing a second throwaway endpoint would just be more to delete later for no benefit over reusing the one that already exists |

## Components

### `core/redis_client.py` (new)

`get_redis_client() -> redis.asyncio.Redis` — a lazily-initialized module-level singleton,
mirroring `core/db.py`'s `get_engine()` caching pattern. Replaces `api/main.py`'s current ad-hoc
`aioredis.from_url(...)` call inside `_check_redis`, so the health check and the rate limiter share
one client instead of each managing their own connection.

### `api/errors.py` (modified)

`ApiError.__init__` gains an optional `headers: dict[str, str] | None = None` parameter, passed
through to `HTTPException`'s existing `headers` field (FastAPI already merges these into the
response). Needed so a 429 can carry `Retry-After`. Fully backward-compatible — API-02's existing
401 usage passes no `headers` and is unaffected.

### `api/middleware/rate_limit.py` (new)

```
async def enforce_rate_limit(key: AuthenticatedKey = Depends(get_current_key)) -> AuthenticatedKey
```

1. `window = current UTC time, formatted YYYYMMDDHHMM` (i.e. truncated to the minute)
2. `redis_key = f"ratelimit:{key.id}:{window}"`
3. `count = await redis.incr(redis_key)`
   - On any Redis error: log a `WARNING` with the exception, return `key` immediately (fail open —
     no limiting applied this request)
4. If `count == 1` (first hit in this window): `await redis.expire(redis_key, 70)` — 60s window
   plus a 10s safety margin, so the key reliably expires even under minor clock/scheduling jitter,
   and never accumulates stale keys in Redis
5. `limit = key.rate_limit_per_minute` (already resolved and defaulted by `get_current_key`/the
   `api_keys` row — no separate fallback needed here, since the column itself defaults to 60 at
   the DB level per FOUND-02)
6. If `count > limit`: raise `ApiError(status_code=429, code="rate_limit_exceeded", message=...,
   headers={"Retry-After": str(seconds_until_next_minute)})`
7. Otherwise: return `key` unchanged

### `api/routers/whoami.py` (modified)

Swaps `Depends(get_current_key)` for `Depends(enforce_rate_limit)` — the route body itself doesn't
change, it's still just echoing back `{role, owner_label}`.

### `api/main.py` (modified)

`_check_redis` calls `core/redis_client.py`'s `get_redis_client()` instead of constructing its own
client inline.

## Data flow

```
Request → RequestIdMiddleware → enforce_rate_limit
              → get_current_key (401 paths unchanged from API-02)
              → INCR ratelimit:{key.id}:{minute}
                  ├─ Redis error ──────→ log WARNING, allow through (fail open)
                  ├─ count > limit ────→ 429 rate_limit_exceeded, Retry-After: <secs>
                  └─ count ≤ limit ────→ pass key through
        → route handler (here: /v1/_whoami) receives AuthenticatedKey
```

## Testing

- `tests/unit/api/test_rate_limit.py` (mocked Redis client, same `AsyncMock` pattern as
  `deps.py`'s DB mocking in API-02): under-limit passes through; over-limit raises `ApiError(429,
  "rate_limit_exceeded")` with a `Retry-After` header; two different keys get independent counters
  (one exceeding its limit never affects the other); a Redis error on `incr` fails open (request
  proceeds, no exception)
- `tests/unit/api/test_whoami_route.py` updated: existing tests patch `enforce_rate_limit`'s Redis
  dependency (or the shared `get_redis_client`) so they keep testing auth wiring without being
  coupled to rate-limit internals
- `tests/unit/core/test_redis_client.py`: `get_redis_client()` returns the same instance across
  calls (singleton behavior), mirroring `core/db.py`'s existing engine-singleton test coverage
  pattern
- Live verification: real Redis (`docker run ... redis:7-alpine`, per API-01/02 precedent), a real
  key minted via the API-02 CLI, hammer `/v1/_whoami` past its `rate_limit_per_minute` and confirm
  a real 429 with `Retry-After`, confirm a second real key is unaffected while the first is still
  limited, then clean up (delete the key, stop containers) — same bar as every prior ticket

## Out of scope (explicitly deferred)

- Sliding-window/token-bucket algorithms — fixed window is the explicit choice for this ticket
- Per-route (vs. global per-key) limits — the ticket scopes this to one limit per key across all
  routes, matching `api_keys.rate_limit_per_minute`'s singular column
- Making Redis-unavailable fail closed — deliberately fails open for now; revisit if it becomes a
  real problem
