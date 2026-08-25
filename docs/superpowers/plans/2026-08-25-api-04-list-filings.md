# API-04 — GET /v1/filings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `GET /v1/filings` — a paginated, filterable list of completed filings, gated by API-03's `enforce_rate_limit`, with an Executive-role caller restricted to High/Critical filings only, and a shared 422 error envelope for invalid query params.

**Architecture:** New `schemas/filings.py` (Pydantic response models) and `api/routers/filings.py` (the route itself, using SQLAlchemy 2.0-style `select()` queries against `Filing` joined to `Brief`). The route depends on `enforce_rate_limit` (API-03) for auth+rate-limiting and on the already-built-but-previously-unused `get_db` FastAPI dependency (`core/db.py`, from FOUND-02) for a request-scoped DB session. `api/errors.py` gains a second exception handler for `RequestValidationError`, closing a pre-existing gap where invalid query params returned FastAPI's default error shape instead of this project's shared envelope.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, Pydantic v2, pytest + pytest-asyncio, `unittest.mock.AsyncMock`/`MagicMock` for DB mocking (established pattern from API-02/API-03).

## Global Constraints

- Python 3.11+, no new third-party dependency
- Ruff and mypy must both pass clean (`ruff check --no-cache .`, `mypy src/`) — run with `--no-cache`; a stale cache caused real confusion on the previous ticket (API-03) on this same repo
- If `ruff` or `mypy` flag anything, fix the actual code — never add/remove a `# noqa` based on a guess about what a rule does or doesn't catch; verify by running the tool on the specific code first. (On API-03, an unverified claim about a ruff rule caused a real regression that took two fix rounds to undo.)
- Only filings with `status = 'complete'` (i.e., a real, persisted `Brief` row) are ever returned
- An Executive-role (`ApiKeyRole.EXECUTIVE`) caller's effective risk filter is always the intersection of whatever `?risk=` they requested (or "any", if omitted) with `{RiskLevel.HIGH, RiskLevel.CRITICAL}` — silently narrowed, never a 403 or error, even when the intersection is empty (empty result set instead)
- The response schema (`id, entity_name, filing_type, domain, risk_level, published_at, executive_brief`) never includes any extraction-shaped field for any role — this endpoint has no extraction data in its response at all
- All commands use the shared project venv at `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python`, with `PYTHONPATH=src` for anything importing the `regradar` package
- Push commits to `origin worktree-api-04-list-filings` as you go (the branch is already pushed once; keep it updated) — the user explicitly asked to see the branch and its progress in the GitHub repo, not just work in a local-only worktree

---

### Task 1: Shared 422 validation-error envelope

**Files:**
- Modify: `src/regradar/api/errors.py`
- Test: `tests/unit/api/test_errors.py`

**Interfaces:**
- Consumes: nothing new
- Produces: nothing new exported — this registers a second exception handler inside the existing `register_error_handlers(app)` function, active automatically for every route in the app (including Task 2's new one) once `create_app()` calls it

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

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
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
            headers=exc.headers,
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

- [ ] **Step 1: Write the failing test**

Add a new route to `_make_app` and a new test. Replace the whole file with:

```python
"""Tests for the shared API error envelope."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from regradar.api.errors import ApiError, register_error_handlers
from regradar.api.middleware.request_id import RequestIdMiddleware


class _Payload(BaseModel):
    count: int


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

    @app.get("/needs-int")
    async def needs_int(count: int):
        return {"count": count}

    @app.post("/needs-payload")
    async def needs_payload(payload: _Payload):
        return payload

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


def test_invalid_query_param_type_renders_shared_envelope():
    response = TestClient(_make_app()).get("/needs-int", params={"count": "not-a-number"})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "detail" not in body


def test_invalid_query_param_includes_request_id():
    response = TestClient(_make_app()).get("/needs-int", params={"count": "not-a-number"})

    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_missing_required_body_field_renders_shared_envelope():
    response = TestClient(_make_app()).post("/needs-payload", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_errors.py -v`
Expected: `test_api_error_renders_envelope`, `test_api_error_includes_request_id_matching_header`,
`test_api_error_includes_custom_headers` PASS (unchanged behavior); the three new tests FAIL —
`test_invalid_query_param_type_renders_shared_envelope` and
`test_missing_required_body_field_renders_shared_envelope` fail because the response body is
FastAPI's default `{"detail": [...]}` shape, not `{"error": {...}}`; `test_invalid_query_param_includes_request_id`
fails with a `KeyError` since there's no `"error"` key in the default shape

- [ ] **Step 3: Write the implementation**

Modify `src/regradar/api/errors.py`. Change:

```python
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from regradar.api.middleware.request_id import request_id_ctx
```

to:

```python
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from regradar.api.middleware.request_id import request_id_ctx
```

and add a second handler inside `register_error_handlers`, right after the existing `_handle_api_error` one:

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

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Invalid request parameters.",
                    "request_id": request_id_ctx.get(),
                }
            },
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_errors.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Run the full unit suite to confirm nothing else broke**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -q`
Expected: all pass — every existing route's 401/429/200 tests are unaffected, since none of them
trigger `RequestValidationError` (they either have no query params or pass valid ones)

- [ ] **Step 6: Lint and type-check**

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check --no-cache src/regradar/api/errors.py tests/unit/api/test_errors.py`
Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/regradar/api/errors.py`
Expected: both clean

- [ ] **Step 7: Commit and push**

```bash
git add src/regradar/api/errors.py tests/unit/api/test_errors.py
git commit -m "Add shared 422 validation-error envelope (API-04 task 1/3)"
git push origin worktree-api-04-list-filings
```

---

### Task 2: `GET /v1/filings` — schemas, query logic, route

**Files:**
- Create: `src/regradar/schemas/filings.py`
- Create: `src/regradar/api/routers/filings.py`
- Modify: `src/regradar/api/main.py`
- Test: `tests/unit/api/test_filings_route.py`

**Interfaces:**
- Consumes: `enforce_rate_limit` from `api/middleware/rate_limit.py` (API-03, existing); `AuthenticatedKey` from `api/deps.py` (API-02, existing); `get_db` from `core/db.py` (existing, `async def get_db() -> AsyncGenerator[AsyncSession, None]`, defined but never used by any route until now); `ApiKeyRole`, `FilingDomain`, `RiskLevel`, `FilingStatus` from `models/enums.py` (existing); `Filing` from `models/filing.py`, `Brief` from `models/brief.py` (existing)
- Produces: `FilingListItem(BaseModel)`, `FilingListResponse(BaseModel)` in `schemas/filings.py`; `router: APIRouter` in `api/routers/filings.py`, mounted in `main.py`

Current `src/regradar/models/filing.py` fields relevant to this task: `id: uuid.UUID`, `entity_name:
str`, `filing_type: str`, `domain: FilingDomain | None`, `risk_level: RiskLevel | None`,
`published_at: datetime`, `status: FilingStatus`. Relationship: `brief: Mapped["Brief | None"]`.

Current `src/regradar/models/brief.py` fields relevant to this task: `filing_id: uuid.UUID`
(unique FK to `filings.id`), `executive_brief: str` (`nullable=False`).

Current `src/regradar/api/main.py` (full file, for context on where to add the new router):

```python
"""FastAPI application entrypoint: app factory, health check, and core middleware."""

import logging
from importlib.metadata import version

from fastapi import FastAPI, Response
from sqlalchemy import text

from regradar.api.errors import register_error_handlers
from regradar.api.middleware.request_id import RequestIdFilter, RequestIdMiddleware
from regradar.api.routers.whoami import router as whoami_router
from regradar.core.db import get_engine
from regradar.core.redis_client import get_redis_client

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
    try:
        return bool(await get_redis_client().ping())
    except Exception:  # noqa: BLE001 — health check must degrade, not raise
        return False


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

Current `src/regradar/api/middleware/rate_limit.py`'s exported signature (already complete, do not
modify):

```python
async def enforce_rate_limit(key: AuthenticatedKey = Depends(get_current_key)) -> AuthenticatedKey
```

Current `src/regradar/core/db.py`'s exported `get_db` (already complete, do not modify):

```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a request-scoped database session."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session
```

Note: `get_db` internally calls `get_session_factory()` (imported at module level in `core/db.py`
from... itself — `get_session_factory` is defined in the same file). To mock the database in tests,
monkeypatch `regradar.core.db.get_session_factory` directly (not a per-module re-import, since
`get_db` looks up `get_session_factory` in its own module's namespace at call time) — see the test
step below for the exact pattern.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/api/test_filings_route.py`:

```python
"""Tests for GET /v1/filings."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.models.enums import ApiKeyRole, FilingDomain, FilingStatus, RiskLevel


def _authenticated_key_row(role: ApiKeyRole):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.role = role
    row.owner_label = "test-owner"
    row.rate_limit_per_minute = 1000
    row.is_active = True
    return row


def _filing_row(
    *,
    entity_name: str = "Acme Corp",
    filing_type: str = "10-K",
    domain: FilingDomain = FilingDomain.FINANCIAL,
    risk_level: RiskLevel = RiskLevel.MEDIUM,
    published_at: datetime | None = None,
    status: FilingStatus = FilingStatus.COMPLETE,
):
    filing = MagicMock()
    filing.id = uuid.uuid4()
    filing.entity_name = entity_name
    filing.filing_type = filing_type
    filing.domain = domain
    filing.risk_level = risk_level
    filing.published_at = published_at or datetime(2026, 1, 1, tzinfo=UTC)
    filing.status = status
    return filing


def _mock_db(monkeypatch: pytest.MonkeyPatch, *, total: int, rows: list[tuple]):
    """rows: list of (filing_mock, executive_brief_str) tuples, as the join query returns."""
    mock_db = AsyncMock()

    count_result = MagicMock()
    count_result.scalar_one = MagicMock(return_value=total)

    page_result = MagicMock()
    page_result.all = MagicMock(return_value=rows)

    # First execute() call is the COUNT query, second is the page query — matches
    # the route's own call order (count first, then the paginated SELECT).
    mock_db.execute = AsyncMock(side_effect=[count_result, page_result])

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)

    return mock_db


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


def test_list_filings_returns_paginated_response(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    filing = _filing_row()
    _mock_db(monkeypatch, total=1, rows=[(filing, "This is the executive brief.")])

    response = TestClient(create_app()).get(
        "/v1/filings", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert len(body["data"]) == 1
    item = body["data"][0]
    assert item["entity_name"] == "Acme Corp"
    assert item["filing_type"] == "10-K"
    assert item["domain"] == "financial"
    assert item["risk_level"] == "medium"
    assert item["executive_brief"] == "This is the executive brief."


def test_list_filings_response_never_includes_extraction_fields(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    filing = _filing_row()
    _mock_db(monkeypatch, total=1, rows=[(filing, "brief text")])

    response = TestClient(create_app()).get(
        "/v1/filings", headers={"Authorization": "Bearer rr_test-key"}
    )

    item_keys = set(response.json()["data"][0].keys())
    extraction_shaped_keys = {"obligations", "deadlines", "risk_flags", "affected_products",
                               "key_entities", "competitor_mentions"}
    assert item_keys.isdisjoint(extraction_shaped_keys)
    assert item_keys == {
        "id", "entity_name", "filing_type", "domain", "risk_level", "published_at",
        "executive_brief",
    }


def test_list_filings_without_auth_header_returns_401(monkeypatch: pytest.MonkeyPatch):
    response = TestClient(create_app()).get("/v1/filings")

    assert response.status_code == 401


def test_list_filings_domain_filter_applies_to_query(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_db(monkeypatch, total=0, rows=[])

    TestClient(create_app()).get(
        "/v1/filings",
        params={"domain": "clinical"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    # Both the COUNT and the page SELECT must carry the domain filter — compile
    # each captured statement and confirm the filing_domain column is referenced.
    for call in mock_db.execute.call_args_list:
        compiled = str(call.args[0].compile(compile_kwargs={"literal_binds": False}))
        assert "domain" in compiled


def test_list_filings_invalid_domain_returns_422(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_db(monkeypatch, total=0, rows=[])

    response = TestClient(create_app()).get(
        "/v1/filings",
        params={"domain": "not-a-real-domain"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_executive_role_without_risk_param_only_sees_high_critical(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.EXECUTIVE)
    mock_db = _mock_db(monkeypatch, total=0, rows=[])

    TestClient(create_app()).get(
        "/v1/filings", headers={"Authorization": "Bearer rr_test-key"}
    )

    count_stmt = mock_db.execute.call_args_list[0].args[0]
    compiled = str(count_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "'high'" in compiled or "high" in compiled.lower()
    assert "'critical'" in compiled or "critical" in compiled.lower()
    assert "'low'" not in compiled.lower()
    assert "'medium'" not in compiled.lower()


def test_executive_role_requesting_low_risk_gets_empty_result_not_error(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.EXECUTIVE)
    _mock_db(monkeypatch, total=0, rows=[])

    response = TestClient(create_app()).get(
        "/v1/filings",
        params={"risk": "low"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == []
    assert response.json()["total"] == 0


def test_non_executive_role_is_unaffected_by_executive_risk_restriction(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST)
    mock_db = _mock_db(monkeypatch, total=0, rows=[])

    TestClient(create_app()).get(
        "/v1/filings",
        params={"risk": "low"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    count_stmt = mock_db.execute.call_args_list[0].args[0]
    compiled = str(count_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "low" in compiled.lower()


def test_page_size_over_max_returns_422(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_db(monkeypatch, total=0, rows=[])

    response = TestClient(create_app()).get(
        "/v1/filings",
        params={"page_size": 101},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 422


def test_list_filings_since_filter_applies_to_query(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_db(monkeypatch, total=0, rows=[])

    TestClient(create_app()).get(
        "/v1/filings",
        params={"since": "2026-01-01T00:00:00Z"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    for call in mock_db.execute.call_args_list:
        compiled = str(call.args[0].compile(compile_kwargs={"literal_binds": False}))
        assert "published_at" in compiled


def test_list_filings_query_only_selects_complete_status_joined_to_briefs(
    monkeypatch: pytest.MonkeyPatch,
):
    """Structural proof that the query mechanically excludes incomplete filings:
    status=complete is always in the WHERE clause, and the page query is an
    inner join to briefs (a filing with no Brief row can never match an inner
    join, regardless of any other filter) — this is what actually keeps a
    still-processing filing (no Brief yet) out of every possible result,
    verified end-to-end with real data in Task 3's live verification.
    """
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_db(monkeypatch, total=0, rows=[])

    TestClient(create_app()).get(
        "/v1/filings", headers={"Authorization": "Bearer rr_test-key"}
    )

    count_stmt, page_stmt = (call.args[0] for call in mock_db.execute.call_args_list)
    count_compiled = str(count_stmt.compile(compile_kwargs={"literal_binds": True}))
    page_compiled = str(page_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "complete" in count_compiled.lower()
    assert "complete" in page_compiled.lower()
    assert "join briefs" in page_compiled.lower() or "join public.briefs" in page_compiled.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_filings_route.py -v`
Expected: FAIL — `test_list_filings_without_auth_header_returns_401` gets 404 (no `/v1/filings`
route mounted yet, so no auth dependency runs at all); every other test also fails with 404 for the
same reason

- [ ] **Step 3: Write the schema module**

Create `src/regradar/schemas/filings.py`:

```python
"""Pydantic response models for GET /v1/filings."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from regradar.models.enums import FilingDomain, RiskLevel


class FilingListItem(BaseModel):
    id: uuid.UUID
    entity_name: str
    filing_type: str
    domain: FilingDomain | None
    risk_level: RiskLevel | None
    published_at: datetime
    executive_brief: str


class FilingListResponse(BaseModel):
    data: list[FilingListItem]
    page: int
    page_size: int
    total: int
```

- [ ] **Step 4: Write the router**

Create `src/regradar/api/routers/filings.py`:

```python
"""GET /v1/filings — paginated, filterable list of completed filings.

Only filings with status="complete" are returned (they're the only ones
with a real Brief row to source executive_brief from). An Executive-role
caller's effective risk filter is always intersected with {HIGH, CRITICAL}
— silently narrowed, never a 403 — per the Security & Access Document's
permission matrix (the ticket's own AI Coding Prompt only describes the
field-level restriction, which this endpoint's response schema already
satisfies by construction: it never includes extraction data for any
role).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.deps import AuthenticatedKey
from regradar.api.middleware.rate_limit import enforce_rate_limit
from regradar.core.db import get_db
from regradar.models.brief import Brief
from regradar.models.enums import ApiKeyRole, FilingDomain, FilingStatus, RiskLevel
from regradar.models.filing import Filing
from regradar.schemas.filings import FilingListItem, FilingListResponse

router = APIRouter()

_EXECUTIVE_ALLOWED_RISK_LEVELS = {RiskLevel.HIGH, RiskLevel.CRITICAL}


def _build_filters(
    *,
    role: ApiKeyRole,
    domain: FilingDomain | None,
    risk: RiskLevel | None,
    since: datetime | None,
) -> list:
    filters: list = [Filing.status == FilingStatus.COMPLETE]

    if domain is not None:
        filters.append(Filing.domain == domain)

    if role == ApiKeyRole.EXECUTIVE:
        # Intersect the requested risk (or "any") with the Executive-allowed
        # set. An empty intersection produces Filing.risk_level.in_(set())
        # which SQLAlchemy compiles to an always-false clause — an empty
        # result, not an error.
        effective_risk = (
            {risk} & _EXECUTIVE_ALLOWED_RISK_LEVELS
            if risk is not None
            else _EXECUTIVE_ALLOWED_RISK_LEVELS
        )
        filters.append(Filing.risk_level.in_(effective_risk))
    elif risk is not None:
        filters.append(Filing.risk_level == risk)

    if since is not None:
        filters.append(Filing.published_at >= since)

    return filters


@router.get("/v1/filings", response_model=FilingListResponse)
async def list_filings(
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    domain: FilingDomain | None = Query(default=None),
    risk: RiskLevel | None = Query(default=None),
    since: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> FilingListResponse:
    filters = _build_filters(role=key.role, domain=domain, risk=risk, since=since)

    total_stmt = select(func.count()).select_from(Filing).where(and_(*filters))
    total = (await db.execute(total_stmt)).scalar_one()

    page_stmt = (
        select(Filing, Brief.executive_brief)
        .join(Brief, Brief.filing_id == Filing.id)
        .where(and_(*filters))
        .order_by(Filing.published_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = (await db.execute(page_stmt)).all()

    data = [
        FilingListItem(
            id=filing.id,
            entity_name=filing.entity_name,
            filing_type=filing.filing_type,
            domain=filing.domain,
            risk_level=filing.risk_level,
            published_at=filing.published_at,
            executive_brief=executive_brief,
        )
        for filing, executive_brief in rows
    ]

    return FilingListResponse(data=data, page=page, page_size=page_size, total=total)
```

- [ ] **Step 5: Mount the router**

Modify `src/regradar/api/main.py`. Change:

```python
from regradar.api.routers.whoami import router as whoami_router
```

to:

```python
from regradar.api.routers.filings import router as filings_router
from regradar.api.routers.whoami import router as whoami_router
```

and change:

```python
    app.include_router(whoami_router)
```

to:

```python
    app.include_router(whoami_router)
    app.include_router(filings_router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_filings_route.py -v`
Expected: PASS (11 passed)

- [ ] **Step 7: Run the full unit suite**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -q`
Expected: all pass

- [ ] **Step 8: Lint and type-check**

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check --no-cache .`
Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/`
Expected: both clean, repo-wide (not just the new files — API-03's history showed a file-scoped
check can pass while the repo-wide one doesn't)

If ruff or mypy flag anything, fix the actual code — verify with the tool's own output what's being
flagged and why before deciding how to respond; do not guess or add/remove a `# noqa` based on
pattern-matching against other files.

- [ ] **Step 9: Commit and push**

```bash
git add src/regradar/schemas/filings.py src/regradar/api/routers/filings.py src/regradar/api/main.py tests/unit/api/test_filings_route.py
git commit -m "Add GET /v1/filings with Executive risk restriction (API-04 task 2/3)"
git push origin worktree-api-04-list-filings
```

---

### Task 3: Live verification against real infrastructure

**Files:** None (no code changes — this proves the previous two tasks work against real
infrastructure, matching every prior ticket's live-verification bar).

**Interfaces:** None.

- [ ] **Step 1: Start real Postgres**

Run: `docker start infra-postgres-1` (or `docker compose -f infra/docker-compose.yml up -d
postgres` if that container doesn't exist)

Copy the real `.env`: `cp /Users/SHREYPATEL/Documents/RegRadar/.env .env` (run from the worktree
root)

Confirm migrations are current: `DATABASE_URL="postgresql+asyncpg://regradar:regradar@localhost:5432/regradar"
PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m alembic upgrade head`

- [ ] **Step 2: Insert real test filings and briefs directly via SQL**

Use `docker exec infra-postgres-1 psql -U regradar -d regradar -c "..."` to insert 4 real rows into
`filings` and matching rows into `briefs`, covering the cases the tests need to distinguish:

1. A `financial` domain, `medium` risk, `complete` status filing WITH a brief — should appear in
   default (Admin) results, should NOT appear for an Executive caller
2. A `clinical` domain, `high` risk, `complete` status filing WITH a brief — should appear for
   both Admin and Executive callers
3. A `financial` domain, `critical` risk, `complete` status filing WITH a brief — should appear
   for both
4. A `financial` domain, `medium` risk, `classifying` status filing (still processing) with NO
   brief row — should never appear in any result, for any role

Use real UUIDs (`gen_random_uuid()` if the `pgcrypto`/`uuid-ossp` extension is available, or
generate UUIDs client-side and pass them as literals) and real timestamps spread across a few days
so the `since` filter has something real to narrow. Write the actual SQL you ran into your final
report so it's reproducible.

- [ ] **Step 3: Mint two real API keys**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m regradar.cli
create-api-key --owner-label "live-verification-admin" --role admin`

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m regradar.cli
create-api-key --owner-label "live-verification-executive" --role executive`

Copy both plaintext keys for the next step.

- [ ] **Step 4: Boot the real app and verify against real data**

Run (background): `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m
uvicorn regradar.api.main:app --port 8126`

Run and inspect each:

1. `curl -s http://127.0.0.1:8126/v1/filings -H "Authorization: Bearer <admin key>"` — expect
   `total: 3` (rows 1–3, row 4 excluded), `data` has 3 items, none contain any extraction-shaped
   key
2. `curl -s "http://127.0.0.1:8126/v1/filings?domain=clinical" -H "Authorization: Bearer <admin
   key>"` — expect `total: 1` (only row 2)
3. `curl -s http://127.0.0.1:8126/v1/filings -H "Authorization: Bearer <executive key>"` — expect
   `total: 2` (rows 2 and 3 only — row 1 is `medium` risk, excluded for Executive)
4. `curl -s "http://127.0.0.1:8126/v1/filings?risk=low" -H "Authorization: Bearer <executive
   key>"` — expect `total: 0`, `data: []`, status `200` (not an error)
5. `curl -s "http://127.0.0.1:8126/v1/filings?domain=not-real" -H "Authorization: Bearer <admin
   key>"` — expect status `422` with the shared `{"error": {"code": "validation_error", ...}}`
   envelope
6. `curl -s "http://127.0.0.1:8126/v1/filings?page_size=2&page=1" -H "Authorization: Bearer
   <admin key>"` then `page=2` — confirm together they cover all 3 admin-visible rows with no
   overlap or gap

- [ ] **Step 5: Clean up**

Stop the uvicorn process (kill the background job from Step 4).

Delete the live-verification data:

Run: `docker exec infra-postgres-1 psql -U regradar -d regradar -c "DELETE FROM api_keys WHERE
owner_label LIKE 'live-verification-%';"`

Run: `docker exec infra-postgres-1 psql -U regradar -d regradar -c "DELETE FROM filings WHERE
entity_name LIKE 'Live Verification%';"` (use whatever `entity_name` prefix you actually used in
Step 2 — deleting `filings` cascades to `briefs` via the FK's `ON DELETE CASCADE`, per
`briefs.filing_id`'s definition in `models/brief.py`)

Remove the copied `.env`: `rm -f .env`

Stop Postgres if it wasn't already running for other work: `docker stop infra-postgres-1`

- [ ] **Step 6: Final full-suite check**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -q`
Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check --no-cache .`
Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/`
Expected: all three clean

- [ ] **Step 7: Push the final state**

```bash
git push origin worktree-api-04-list-filings
```

Report back to the user with a summary and ask before merging to `master` — do not merge without
explicit go-ahead, per this project's established workflow (see
`docs/superpowers/specs/2026-08-25-api-04-list-filings-design.md` and every prior ticket's merge
pattern).
