# API-03 — Rate Limiting Middleware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce each API key's `rate_limit_per_minute` via a Redis-backed fixed-window counter, applied through one combined dependency so every future authenticated route gets it automatically, failing open if Redis itself is unavailable.

**Architecture:** A new `core/redis_client.py` provides a process-wide singleton async Redis client (mirroring `core/db.py`'s engine-singleton pattern). A new `api/middleware/rate_limit.py`'s `enforce_rate_limit(key: AuthenticatedKey = Depends(get_current_key)) -> AuthenticatedKey` does one `INCR` on a Redis key scoped to `{api_key_id}:{current_minute}`, TTLs it on first hit, and raises a 429 (`ApiError`, extended with an optional `headers` param for `Retry-After`) if the count exceeds the key's limit. API-02's throwaway `GET /v1/_whoami` route swaps its dependency from `get_current_key` to `enforce_rate_limit` so the whole chain is live-testable today, same as API-02 did for auth alone.

**Tech Stack:** FastAPI, `redis.asyncio` (already a dependency via `redis>=5.0`), pytest + pytest-asyncio, `unittest.mock.AsyncMock`/`MagicMock` for Redis/DB mocking (established pattern from API-02).

## Global Constraints

- Python 3.11+, no new third-party dependency — `redis>=5.0` already provides `redis.asyncio`
- Ruff and mypy must both pass clean (`ruff check .`, `mypy src/`)
- `except Exception` blocks need `# noqa: BLE001 — <reason>`, matching the established repo pattern
- Fixed-window algorithm only (not sliding window / token bucket) — confirmed with the user
- Fail open on Redis errors during the rate-limit check — confirmed with the user, log a `WARNING`, don't block the request
- All commands below use the shared project venv at `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python`, with `PYTHONPATH=src` for anything importing the `regradar` package
- Every task runs from the worktree root (wherever this branch's worktree is checked out)

---

### Task 1: Shared Redis client singleton (`core/redis_client.py`)

**Files:**
- Create: `src/regradar/core/redis_client.py`
- Modify: `src/regradar/api/main.py` (make `_check_redis` use the shared client instead of building its own)
- Modify: `tests/conftest.py` (add a singleton-reset fixture, same pattern as the existing DB engine one)
- Test: `tests/unit/core/test_redis_client.py`

**Interfaces:**
- Produces: `get_redis_client() -> redis.asyncio.Redis` — consumed by Task 3 (`api/middleware/rate_limit.py`) and by `api/main.py`'s `_check_redis`

Current `src/regradar/api/main.py` (full file):

```python
"""FastAPI application entrypoint: app factory, health check, and core middleware."""

import logging
from importlib.metadata import version

import redis.asyncio as aioredis
from fastapi import FastAPI, Response
from sqlalchemy import text

from regradar.api.errors import register_error_handlers
from regradar.api.middleware.request_id import RequestIdFilter, RequestIdMiddleware
from regradar.api.routers.whoami import router as whoami_router
from regradar.core.config import get_settings
from regradar.core.db import get_engine

logging.getLogger().addFilter(RequestIdFilter())


async def _check_database() -> bool:
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 — health check must degrade, not raise
        return False


async def _check_redis() -> bool:
    client = aioredis.from_url(get_settings().redis_url.get_secret_value())
    try:
        return bool(await client.ping())
    except Exception:  # noqa: BLE001 — health check must degrade, not raise
        return False
    finally:
        await client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="RegRadar", version=version("regradar"), docs_url="/docs")
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(whoami_router)

    @app.get("/health")
    async def health(response: Response) -> dict[str, str]:
        db_ok, redis_ok = await _check_database(), await _check_redis()
        if not (db_ok and redis_ok):
            response.status_code = 503
        return {
            "status": "ok" if db_ok and redis_ok else "error",
            "database": "ok" if db_ok else "unreachable",
            "redis": "ok" if redis_ok else "unreachable",
        }

    return app


app = create_app()
```

Current `tests/conftest.py` (full file):

```python
"""Shared pytest fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _no_real_dotenv_fallback(monkeypatch: pytest.MonkeyPatch):
    """Never let a real local .env file leak into test outcomes.

    Settings.model_config sets env_file=".env" so the real app can run with
    just a .env file and no exported env vars. But that means any test that
    simulates "this setting isn't configured" via monkeypatch.delenv(...)
    still silently sees the developer's real .env value as a fallback —
    delenv only clears os.environ, it doesn't disable pydantic-settings'
    dotenv read. On a machine with a populated .env (e.g. real Slack/
    SendGrid credentials for AGENT-10), that turns "not configured" tests
    into false failures/passes depending on what's in that file. Force
    env_file=None for the whole test session so only explicit
    monkeypatch.setenv/os.environ values (and field defaults) apply,
    matching the isolation tests/unit/test_config.py already gets for free
    via its own explicit Settings(_env_file=None) construction.
    """
    from regradar.core.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture(autouse=True)
def _reset_db_engine_singleton():
    """Reset core.db's cached engine/session factory before and after each test.

    pytest-asyncio gives each test function its own event loop by default.
    core.db.get_engine() caches the SQLAlchemy async engine as a module-level
    singleton (correct for a real long-running process, which only ever has
    one event loop for its whole lifetime) — but reused across test functions
    it causes "Future attached to a different loop" errors, since the
    connection pool created in one test's loop can't be used from another
    test's loop. Resetting to None forces a fresh engine bound to the
    current test's loop on next use.
    """
    import regradar.core.db as db_module

    db_module._engine = None
    db_module._session_factory = None
    yield
    db_module._engine = None
    db_module._session_factory = None
```

- [ ] **Step 1: Write the failing test**

Create `tests/unit/core/test_redis_client.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/core/test_redis_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.core.redis_client'`

- [ ] **Step 3: Write the implementation**

Create `src/regradar/core/redis_client.py`:

```python
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
```

Modify `src/regradar/api/main.py`: replace the `_check_redis` function and its imports. Change:

```python
import redis.asyncio as aioredis
from fastapi import FastAPI, Response
from sqlalchemy import text

from regradar.api.errors import register_error_handlers
from regradar.api.middleware.request_id import RequestIdFilter, RequestIdMiddleware
from regradar.api.routers.whoami import router as whoami_router
from regradar.core.config import get_settings
from regradar.core.db import get_engine
```

to:

```python
from fastapi import FastAPI, Response
from sqlalchemy import text

from regradar.api.errors import register_error_handlers
from regradar.api.middleware.request_id import RequestIdFilter, RequestIdMiddleware
from regradar.api.routers.whoami import router as whoami_router
from regradar.core.db import get_engine
from regradar.core.redis_client import get_redis_client
```

and change:

```python
async def _check_redis() -> bool:
    client = aioredis.from_url(get_settings().redis_url.get_secret_value())
    try:
        return bool(await client.ping())
    except Exception:  # noqa: BLE001 — health check must degrade, not raise
        return False
    finally:
        await client.aclose()
```

to:

```python
async def _check_redis() -> bool:
    try:
        return bool(await get_redis_client().ping())
    except Exception:  # noqa: BLE001 — health check must degrade, not raise
        return False
```

(The `finally: await client.aclose()` is removed — the client is now a shared singleton, not a
per-call connection, so `_check_redis` must not close it out from under the rest of the process.)

Modify `tests/conftest.py`: add a new autouse fixture right after `_reset_db_engine_singleton`:

```python


@pytest.fixture(autouse=True)
def _reset_redis_client_singleton():
    """Reset core.redis_client's cached client before and after each test.

    Same reasoning as _reset_db_engine_singleton above — a cached client
    tied to one test's event loop breaks when reused from another test's
    loop, and tests that monkeypatch settings shouldn't leak a
    previously-built client into a later test.
    """
    import regradar.core.redis_client as redis_client_module

    redis_client_module._client = None
    yield
    redis_client_module._client = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/core/test_redis_client.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full unit suite to confirm nothing else broke**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -q`
Expected: all pass — `tests/unit/api/test_main.py`'s health-check tests monkeypatch `_check_database`/`_check_redis` directly, so they're unaffected by `_check_redis`'s internals changing

- [ ] **Step 6: Lint and type-check**

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check src/regradar/core/redis_client.py src/regradar/api/main.py tests/unit/core/test_redis_client.py tests/conftest.py`
Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/regradar/core/redis_client.py src/regradar/api/main.py`
Expected: both clean

- [ ] **Step 7: Commit**

```bash
git add src/regradar/core/redis_client.py src/regradar/api/main.py tests/conftest.py tests/unit/core/test_redis_client.py
git commit -m "Add shared Redis client singleton (API-03 task 1/6)"
```

---

### Task 2: `ApiError` gains an optional `headers` param

**Files:**
- Modify: `src/regradar/api/errors.py`
- Test: `tests/unit/api/test_errors.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `ApiError(status_code: int, code: str, message: str, headers: dict[str, str] | None = None)` — consumed by Task 3 (`api/middleware/rate_limit.py`, to attach `Retry-After` to its 429)

Current `src/regradar/api/errors.py` (full file):

```python
"""Shared error envelope for the RegRadar API.

Every error response takes the same shape:
{"error": {"code": "...", "message": "...", "request_id": "..."}}
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from regradar.api.middleware.request_id import request_id_ctx


class ApiError(HTTPException):
    """An HTTPException that renders through the shared error envelope."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.detail,
                    "request_id": request_id_ctx.get(),
                }
            },
        )
```

Current `tests/unit/api/test_errors.py` (full file):

```python
"""Tests for the shared API error envelope."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from regradar.api.errors import ApiError, register_error_handlers
from regradar.api.middleware.request_id import RequestIdMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise ApiError(status_code=401, code="invalid_api_key", message="bad key")

    return app


def test_api_error_renders_envelope():
    response = TestClient(_make_app()).get("/boom")

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "invalid_api_key"
    assert body["error"]["message"] == "bad key"


def test_api_error_includes_request_id_matching_header():
    response = TestClient(_make_app()).get("/boom")

    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
```

- [ ] **Step 1: Write the failing test**

Modify `tests/unit/api/test_errors.py`: add a second route to `_make_app` and a new test. Replace the whole file with:

```python
"""Tests for the shared API error envelope."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from regradar.api.errors import ApiError, register_error_handlers
from regradar.api.middleware.request_id import RequestIdMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise ApiError(status_code=401, code="invalid_api_key", message="bad key")

    @app.get("/boom-with-headers")
    async def boom_with_headers():
        raise ApiError(
            status_code=429,
            code="rate_limit_exceeded",
            message="slow down",
            headers={"Retry-After": "42"},
        )

    return app


def test_api_error_renders_envelope():
    response = TestClient(_make_app()).get("/boom")

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "invalid_api_key"
    assert body["error"]["message"] == "bad key"


def test_api_error_includes_request_id_matching_header():
    response = TestClient(_make_app()).get("/boom")

    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_api_error_includes_custom_headers():
    response = TestClient(_make_app()).get("/boom-with-headers")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "42"
    assert response.json()["error"]["code"] == "rate_limit_exceeded"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_errors.py -v`
Expected: FAIL — `test_api_error_includes_custom_headers` errors with `TypeError: ApiError.__init__() got an unexpected keyword argument 'headers'`

- [ ] **Step 3: Write the implementation**

Modify `src/regradar/api/errors.py`. Change:

```python
class ApiError(HTTPException):
    """An HTTPException that renders through the shared error envelope."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
```

to:

```python
class ApiError(HTTPException):
    """An HTTPException that renders through the shared error envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code
```

The exception handler in the same file needs no change — FastAPI's `HTTPException.headers` is
already merged into the response automatically once set on the exception, independent of what the
handler's `JSONResponse` itself constructs.

Actually, verify this assumption before trusting it: FastAPI's default behavior merges
`HTTPException.headers` into the response only when its *default* exception handler runs — a
**custom** handler (like the one this file registers) builds its own `JSONResponse` and must copy
`exc.headers` onto it explicitly, or they're silently dropped. Update the handler too. Change:

```python
def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.detail,
                    "request_id": request_id_ctx.get(),
                }
            },
        )
```

to:

```python
def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.detail,
                    "request_id": request_id_ctx.get(),
                }
            },
            headers=exc.headers,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_errors.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full unit suite to confirm the 401 path (no headers) still works**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -q`
Expected: all pass — API-02's `test_deps.py`/`test_whoami_route.py` construct `ApiError` without
`headers`, which now defaults to `None`; `JSONResponse(..., headers=None)` is valid and behaves
exactly as it did with no `headers` argument at all

- [ ] **Step 6: Lint and type-check**

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check src/regradar/api/errors.py tests/unit/api/test_errors.py`
Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/regradar/api/errors.py`
Expected: both clean

- [ ] **Step 7: Commit**

```bash
git add src/regradar/api/errors.py tests/unit/api/test_errors.py
git commit -m "Add optional headers support to ApiError (API-03 task 2/6)"
```

---

### Task 3: Rate limit dependency (`api/middleware/rate_limit.py`)

**Files:**
- Create: `src/regradar/api/middleware/rate_limit.py`
- Test: `tests/unit/api/test_rate_limit.py`

**Interfaces:**
- Consumes: `get_redis_client` from `core/redis_client.py` (Task 1); `ApiError` from `api/errors.py` with its new `headers` param (Task 2); `AuthenticatedKey`, `get_current_key` from `api/deps.py` (existing, API-02)
- Produces: `async def enforce_rate_limit(key: AuthenticatedKey = Depends(get_current_key)) -> AuthenticatedKey` — consumed by Task 4 (`api/routers/whoami.py`)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/api/test_rate_limit.py`:

```python
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


def _patch_redis(monkeypatch: pytest.MonkeyPatch, *, incr_side_effect):
    mock_client = MagicMock()
    mock_client.incr = AsyncMock(side_effect=incr_side_effect)
    mock_client.expire = AsyncMock()
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


async def test_subsequent_hit_does_not_reset_expiry(monkeypatch: pytest.MonkeyPatch):
    mock_client = _patch_redis(monkeypatch, incr_side_effect=[2])
    key = _make_key(rate_limit_per_minute=5)

    await rate_limit_module.enforce_rate_limit(key=key)

    mock_client.expire.assert_not_awaited()


async def test_over_limit_raises_429_with_retry_after(monkeypatch: pytest.MonkeyPatch):
    _patch_redis(monkeypatch, incr_side_effect=[6])
    key = _make_key(rate_limit_per_minute=5)

    with pytest.raises(ApiError) as exc_info:
        await rate_limit_module.enforce_rate_limit(key=key)

    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "rate_limit_exceeded"
    assert exc_info.value.headers is not None
    assert "Retry-After" in exc_info.value.headers


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_rate_limit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.api.middleware.rate_limit'`

- [ ] **Step 3: Write the implementation**

Create `src/regradar/api/middleware/rate_limit.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_rate_limit.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Lint and type-check**

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check src/regradar/api/middleware/rate_limit.py tests/unit/api/test_rate_limit.py`
Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/regradar/api/middleware/rate_limit.py`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/regradar/api/middleware/rate_limit.py tests/unit/api/test_rate_limit.py
git commit -m "Add Redis-backed rate limit dependency (API-03 task 3/6)"
```

---

### Task 4: Wire `enforce_rate_limit` into `GET /v1/_whoami`

**Files:**
- Modify: `src/regradar/api/routers/whoami.py`
- Modify: `tests/unit/api/test_whoami_route.py`

**Interfaces:**
- Consumes: `enforce_rate_limit` from `api/middleware/rate_limit.py` (Task 3)

Current `src/regradar/api/routers/whoami.py` (full file):

```python
"""Throwaway route exercising get_current_key over real HTTP.

No real authenticated route exists yet (API-04 etc. aren't built), so this
is what API-02's own acceptance criteria (missing/malformed/unknown/revoked/
valid key, all via HTTP) actually run against. Delete this file, and its
mount point in api/main.py, once a real authenticated route takes over that
job.
"""

from fastapi import APIRouter, Depends

from regradar.api.deps import AuthenticatedKey, get_current_key

router = APIRouter()


@router.get("/v1/_whoami")
async def whoami(key: AuthenticatedKey = Depends(get_current_key)) -> dict[str, str]:
    return {"role": key.role.value, "owner_label": key.owner_label}
```

Current `tests/unit/api/test_whoami_route.py` (full file):

```python
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
```

- [ ] **Step 1: Write the failing test**

Replace `tests/unit/api/test_whoami_route.py` entirely with:

```python
"""Smoke tests confirming get_current_key + enforce_rate_limit are wired to a real route.

The exhaustive missing/malformed/unknown/revoked/valid-key behavior is
already covered in test_deps.py, and the exhaustive rate-limiting behavior
is covered in test_rate_limit.py — this file only confirms the wiring
itself works end-to-end over real HTTP.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.models.enums import ApiKeyRole


def _mock_db_session(monkeypatch: pytest.MonkeyPatch, row):
    mock_db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)
    mock_db.execute = AsyncMock(return_value=result)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(deps_module, "get_session_factory", lambda: mock_session_factory)


def _mock_redis(monkeypatch: pytest.MonkeyPatch, *, incr_return_value: int):
    mock_redis = MagicMock()
    mock_redis.incr = AsyncMock(return_value=incr_return_value)
    mock_redis.expire = AsyncMock()
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: mock_redis)


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
    _mock_db_session(monkeypatch, row)
    _mock_redis(monkeypatch, incr_return_value=1)

    response = TestClient(create_app()).get(
        "/v1/_whoami", headers={"Authorization": "Bearer rr_valid-test-key"}
    )

    assert response.status_code == 200
    assert response.json() == {"role": "eng_lead", "owner_label": "test-integrator"}


def test_whoami_returns_429_when_rate_limit_exceeded(monkeypatch: pytest.MonkeyPatch):
    row = MagicMock()
    row.id = uuid4()
    row.role = ApiKeyRole.ANALYST
    row.owner_label = "test-owner"
    row.rate_limit_per_minute = 5
    row.is_active = True
    _mock_db_session(monkeypatch, row)
    _mock_redis(monkeypatch, incr_return_value=6)

    response = TestClient(create_app()).get(
        "/v1/_whoami", headers={"Authorization": "Bearer rr_valid-test-key"}
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limit_exceeded"
    assert "retry-after" in {h.lower() for h in response.headers}
```

- [ ] **Step 2: Run tests to verify the new test fails, others still pass**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_whoami_route.py -v`
Expected: `test_whoami_without_header_returns_401` and `test_whoami_with_valid_key_returns_role_and_owner`
PASS; `test_whoami_returns_429_when_rate_limit_exceeded` FAILS — the route still uses
`get_current_key` directly, so it never checks the mocked Redis client and returns 200 instead of
429

- [ ] **Step 3: Write the implementation**

Modify `src/regradar/api/routers/whoami.py` to:

```python
"""Throwaway route exercising get_current_key + enforce_rate_limit over real HTTP.

No real authenticated route exists yet (API-04 etc. aren't built), so this
is what API-02's and API-03's own acceptance criteria (missing/malformed/
unknown/revoked/valid key, plus rate limiting, all via HTTP) actually run
against. Delete this file, and its mount point in api/main.py, once a real
authenticated route takes over that job.
"""

from fastapi import APIRouter, Depends

from regradar.api.deps import AuthenticatedKey
from regradar.api.middleware.rate_limit import enforce_rate_limit

router = APIRouter()


@router.get("/v1/_whoami")
async def whoami(key: AuthenticatedKey = Depends(enforce_rate_limit)) -> dict[str, str]:
    return {"role": key.role.value, "owner_label": key.owner_label}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_whoami_route.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full unit suite**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -q`
Expected: all pass

- [ ] **Step 6: Lint and type-check**

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check src/regradar/api/`
Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/regradar/api/`
Expected: both clean

- [ ] **Step 7: Commit**

```bash
git add src/regradar/api/routers/whoami.py tests/unit/api/test_whoami_route.py
git commit -m "Wire enforce_rate_limit into /v1/_whoami (API-03 task 4/6)"
```

---

### Task 5: Extend the CLI with an optional `--rate-limit-per-minute` flag

**Files:**
- Modify: `src/regradar/cli.py`
- Modify: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `_create_api_key(*, owner_label: str, role: str, rate_limit_per_minute: int | None = None) -> None` — the `rate_limit_per_minute` param is new; consumed directly by Task 6's live verification (there's no other code caller — this exists so live verification can mint a key with a low limit like 3/minute and trigger a real 429 in a handful of requests instead of needing 60+)

Current `src/regradar/cli.py` (full file):

```python
"""Local dev entrypoints: run a single filing through the pipeline, run eval suites, etc."""

import argparse
import asyncio

from regradar.core.db import get_session_factory


def _poll_once() -> None:
    """Run a single ingestion cycle across all active sources, then exit."""
    from regradar.ingestion.flows import poll_all_sources

    summary = asyncio.run(poll_all_sources())
    if not summary:
        print("No active sources configured — nothing polled.")
        return

    for source_name, count in summary.items():
        if count == -1:
            print(f"{source_name}: FAILED (see logs)")
        else:
            print(f"{source_name}: {count} new filing(s)")


def _create_api_key(*, owner_label: str, role: str) -> None:
    """Mint a new API key: hash it, insert the row, print the plaintext once.

    This is a bootstrap mechanism for local dev, tests, and live
    verification — no ticket has built a real key-issuance endpoint yet
    (FE-07 defers that to V2).
    """
    from regradar.core.api_keys import generate_api_key, hash_api_key
    from regradar.models.api_key import ApiKey
    from regradar.models.enums import ApiKeyRole

    try:
        role_enum = ApiKeyRole(role)
    except ValueError:
        valid = ", ".join(member.value for member in ApiKeyRole)
        raise SystemExit(f"Invalid role '{role}'. Must be one of: {valid}") from None

    plaintext_key = generate_api_key()

    async def _insert() -> None:
        session_factory = get_session_factory()
        async with session_factory() as db:
            key = ApiKey(
                key_hash=hash_api_key(plaintext_key),
                owner_label=owner_label,
                role=role_enum,
                is_active=True,
            )
            db.add(key)
            await db.commit()

    asyncio.run(_insert())

    print(f"Created API key for '{owner_label}' with role '{role_enum.value}'.")
    print(f"Key (shown once, will not be shown again): {plaintext_key}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="regradar")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "poll-once", help="Run a single ingestion cycle across all active sources, then exit."
    )
    create_key_parser = subparsers.add_parser(
        "create-api-key", help="Mint a new API key and print it once."
    )
    create_key_parser.add_argument("--owner-label", required=True)
    create_key_parser.add_argument("--role", required=True)

    args = parser.parse_args()

    if args.command == "poll-once":
        _poll_once()
    elif args.command == "create-api-key":
        _create_api_key(owner_label=args.owner_label, role=args.role)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

Current `tests/unit/test_cli.py` (full file):

```python
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
```

- [ ] **Step 1: Write the failing test**

Modify `tests/unit/test_cli.py`: add one new test at the end of the file:

```python


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
```

- [ ] **Step 2: Run tests to verify the new tests fail**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/test_cli.py -v`
Expected: `test_create_api_key_with_explicit_rate_limit` FAILS with `TypeError: _create_api_key()
got an unexpected keyword argument 'rate_limit_per_minute'`. `test_create_api_key_without_rate_limit_uses_model_default`
FAILS too, but for a different reason: it calls `_create_api_key` without the new kwarg, which
still matches the current signature, but the captured `ApiKey(...)` instance's
`rate_limit_per_minute` is `None`, not `60` — `mapped_column(..., default=60)` is only applied by
SQLAlchemy at INSERT/flush time, and this mocked test never actually flushes to a real database.
Step 3 fixes both: `_create_api_key` gains the new parameter and always passes an explicit
`rate_limit_per_minute` value to `ApiKey(...)` (60 when the CLI argument is `None`), so the value
is correct immediately on construction rather than depending on a DB-level default a mocked test
can't observe.

- [ ] **Step 3: Write the implementation**

Modify `src/regradar/cli.py`. Change:

```python
def _create_api_key(*, owner_label: str, role: str) -> None:
    """Mint a new API key: hash it, insert the row, print the plaintext once.

    This is a bootstrap mechanism for local dev, tests, and live
    verification — no ticket has built a real key-issuance endpoint yet
    (FE-07 defers that to V2).
    """
    from regradar.core.api_keys import generate_api_key, hash_api_key
    from regradar.models.api_key import ApiKey
    from regradar.models.enums import ApiKeyRole

    try:
        role_enum = ApiKeyRole(role)
    except ValueError:
        valid = ", ".join(member.value for member in ApiKeyRole)
        raise SystemExit(f"Invalid role '{role}'. Must be one of: {valid}") from None

    plaintext_key = generate_api_key()

    async def _insert() -> None:
        session_factory = get_session_factory()
        async with session_factory() as db:
            key = ApiKey(
                key_hash=hash_api_key(plaintext_key),
                owner_label=owner_label,
                role=role_enum,
                is_active=True,
            )
            db.add(key)
            await db.commit()

    asyncio.run(_insert())

    print(f"Created API key for '{owner_label}' with role '{role_enum.value}'.")
    print(f"Key (shown once, will not be shown again): {plaintext_key}")
```

to:

```python
_DEFAULT_RATE_LIMIT_PER_MINUTE = 60  # matches ApiKey.rate_limit_per_minute's DB default (FOUND-02)


def _create_api_key(
    *, owner_label: str, role: str, rate_limit_per_minute: int | None = None
) -> None:
    """Mint a new API key: hash it, insert the row, print the plaintext once.

    This is a bootstrap mechanism for local dev, tests, and live
    verification — no ticket has built a real key-issuance endpoint yet
    (FE-07 defers that to V2). rate_limit_per_minute is optional and mainly
    useful for API-03's live verification, where a low limit (e.g. 3) lets a
    real 429 be triggered in a handful of requests instead of 60+.
    """
    from regradar.core.api_keys import generate_api_key, hash_api_key
    from regradar.models.api_key import ApiKey
    from regradar.models.enums import ApiKeyRole

    try:
        role_enum = ApiKeyRole(role)
    except ValueError:
        valid = ", ".join(member.value for member in ApiKeyRole)
        raise SystemExit(f"Invalid role '{role}'. Must be one of: {valid}") from None

    plaintext_key = generate_api_key()
    resolved_rate_limit = (
        rate_limit_per_minute if rate_limit_per_minute is not None else _DEFAULT_RATE_LIMIT_PER_MINUTE
    )

    async def _insert() -> None:
        session_factory = get_session_factory()
        async with session_factory() as db:
            key = ApiKey(
                key_hash=hash_api_key(plaintext_key),
                owner_label=owner_label,
                role=role_enum,
                is_active=True,
                rate_limit_per_minute=resolved_rate_limit,
            )
            db.add(key)
            await db.commit()

    asyncio.run(_insert())

    print(f"Created API key for '{owner_label}' with role '{role_enum.value}'.")
    print(f"Rate limit: {resolved_rate_limit} requests/minute.")
    print(f"Key (shown once, will not be shown again): {plaintext_key}")
```

Also modify `main()` to add the new CLI flag. Change:

```python
    create_key_parser = subparsers.add_parser(
        "create-api-key", help="Mint a new API key and print it once."
    )
    create_key_parser.add_argument("--owner-label", required=True)
    create_key_parser.add_argument("--role", required=True)

    args = parser.parse_args()

    if args.command == "poll-once":
        _poll_once()
    elif args.command == "create-api-key":
        _create_api_key(owner_label=args.owner_label, role=args.role)
    else:
        parser.print_help()
```

to:

```python
    create_key_parser = subparsers.add_parser(
        "create-api-key", help="Mint a new API key and print it once."
    )
    create_key_parser.add_argument("--owner-label", required=True)
    create_key_parser.add_argument("--role", required=True)
    create_key_parser.add_argument(
        "--rate-limit-per-minute",
        type=int,
        default=None,
        help="Override the key's rate limit (default: 60/minute).",
    )

    args = parser.parse_args()

    if args.command == "poll-once":
        _poll_once()
    elif args.command == "create-api-key":
        _create_api_key(
            owner_label=args.owner_label,
            role=args.role,
            rate_limit_per_minute=args.rate_limit_per_minute,
        )
    else:
        parser.print_help()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/test_cli.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the full unit suite**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -q`
Expected: all pass

- [ ] **Step 6: Lint and type-check**

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check src/regradar/cli.py tests/unit/test_cli.py`
Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/regradar/cli.py`
Expected: both clean

- [ ] **Step 7: Commit**

```bash
git add src/regradar/cli.py tests/unit/test_cli.py
git commit -m "Add optional --rate-limit-per-minute flag to create-api-key (API-03 task 5/6)"
```

---

### Task 6: Live verification against real infrastructure

**Files:** None (no code changes — this proves the previous five tasks work against real
infrastructure, matching every prior ticket's live-verification bar).

**Interfaces:** None.

- [ ] **Step 1: Start real Postgres and Redis**

Run: `docker start infra-postgres-1` (or `docker compose -f infra/docker-compose.yml up -d
postgres` if that container doesn't exist)

Run: `docker run -d --name regradar-verify-redis -p 6379:6379 redis:7-alpine`

Confirm migrations are current: `PYTHONPATH=src DATABASE_URL="postgresql+asyncpg://regradar:regradar@localhost:5432/regradar" /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m alembic upgrade head`

- [ ] **Step 2: Mint a real key with a low rate limit**

Copy the real `.env` from the main checkout so the app has real credentials to boot with:
`cp /Users/SHREYPATEL/Documents/RegRadar/.env .env`

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m regradar.cli
create-api-key --owner-label "live-verification-ratelimit" --role admin
--rate-limit-per-minute 3`

Expected: prints a real `rr_...` key and "Rate limit: 3 requests/minute." Copy the key for the
next step.

- [ ] **Step 3: Boot the real app and hammer /v1/_whoami past its limit**

Run (background): `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m
uvicorn regradar.api.main:app --port 8125`

Run four requests in a row with the real key (the limit is 3, so the 4th must 429):

```bash
for i in 1 2 3 4; do
  echo "=== request $i ==="
  curl -s -i http://127.0.0.1:8125/v1/_whoami -H "Authorization: Bearer <the real key from Step 2>"
  echo
done
```

Expected: requests 1–3 return `200` with `{"role":"admin","owner_label":"live-verification-ratelimit"}`;
request 4 returns `429` with a `Retry-After` header and JSON body
`{"error":{"code":"rate_limit_exceeded",...}}`

- [ ] **Step 4: Confirm a second, different key is unaffected**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m regradar.cli
create-api-key --owner-label "live-verification-ratelimit-2" --role admin`

Run: `curl -s -i http://127.0.0.1:8125/v1/_whoami -H "Authorization: Bearer <the second key>"`

Expected: `200` — the first key being rate-limited has no effect on the second key's independent
counter

- [ ] **Step 5: Confirm the real Redis key exists and is TTL'd**

Run: `docker exec regradar-verify-redis redis-cli KEYS "ratelimit:*"`
Expected: shows a key for the first (limited) API key's id, in the current minute

Run: `docker exec regradar-verify-redis redis-cli TTL "<the key printed above>"`
Expected: a positive integer ≤ 70 (confirms the TTL was actually set, not left to grow unbounded)

- [ ] **Step 6: Clean up**

Stop the uvicorn process (kill the background job from Step 3).

Delete both live-verification keys from the database:

Run: `docker exec infra-postgres-1 psql -U regradar -d regradar -c "DELETE FROM api_keys WHERE
owner_label LIKE 'live-verification-ratelimit%';"`

Remove the copied `.env`: `rm -f .env`

Stop and remove the temporary Redis container: `docker rm -f regradar-verify-redis`

Stop Postgres if it wasn't already running for other work: `docker stop infra-postgres-1`

- [ ] **Step 7: Final full-suite check**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -q`
Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check .`
Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/`
Expected: all three clean

- [ ] **Step 8: Push the branch**

```bash
git push -u origin worktree-api-03-rate-limiting
```

Report back to the user with a summary and ask before merging to `master` — do not merge without
explicit go-ahead, per this project's established workflow (see
`docs/superpowers/specs/2026-08-25-api-03-rate-limiting-design.md` and every prior ticket's merge
pattern).
