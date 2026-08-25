# API-02 — API Key Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Authenticate every API request against a real, hashed, role-bearing `api_keys` row, rejecting missing/malformed/unknown/revoked keys with a consistent error envelope, with a CLI path to actually mint keys to test against.

**Architecture:** A shared `core/api_keys.py` module generates and hashes keys (SHA-256, indexed lookup — no salted per-row iteration). A new `role` enum column lands on `api_keys` via migration. `api/deps.py`'s `get_current_key` FastAPI dependency parses the `Authorization` header, does one indexed DB lookup, and returns a typed `AuthenticatedKey`; failures render through a new shared `ApiError` exception + handler as `{"error": {"code", "message", "request_id"}}`, reusing API-01's request-ID contextvar. A throwaway `GET /v1/_whoami` route mounts the dependency so it's exercisable over real HTTP until a real authenticated route (API-04) exists. A `create-api-key` CLI subcommand is the only way real keys get minted in this ticket's scope.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, Alembic, Pydantic v2, pytest + pytest-asyncio, `hashlib`/`secrets` (stdlib only — no new dependency).

## Global Constraints

- Python 3.11+, existing `pyproject.toml` dependency set — no new third-party dependency needed for this ticket (stdlib `hashlib`/`secrets` cover hashing/generation)
- Every `SAEnum(...)` column must pass `values_callable=pg_enum_values` (existing project rule, `models/enums.py:6-14`)
- Ruff and mypy must both pass clean (`ruff check .`, `mypy src/`) — matches every prior ticket's bar
- `except Exception` blocks (if any) need `# noqa: BLE001 — <reason>`, matching the established repo pattern
- No organization/tenant field — explicitly out of scope per the approved design (see spec)
- No row-level security in this ticket — app-level checks only (SEC-01 is separate, future work)
- Secrets are never logged — plaintext keys only ever appear once, in the CLI's own stdout at creation time, never persisted or returned by any HTTP response

---

### Task 1: Shared key generation/hashing (`core/api_keys.py`)

**Files:**
- Create: `src/regradar/core/api_keys.py`
- Modify: `src/regradar/core/config.py:117` (change `api_key_hash_algorithm` default from `"bcrypt"` to `"sha256"`)
- Modify: `.env.example` (line with `API_KEY_HASH_ALGORITHM=bcrypt` → `API_KEY_HASH_ALGORITHM=sha256`)
- Test: `tests/unit/core/test_api_keys.py`

**Interfaces:**
- Produces: `generate_api_key() -> str` (returns `"rr_" + secrets.token_urlsafe(32)`), `hash_api_key(raw: str) -> str` (returns `hashlib.sha256(raw.encode()).hexdigest()`) — both consumed by Task 4 (`api/deps.py`) and Task 6 (`cli.py`)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/core/__init__.py` (empty file, matches the package-per-test-dir convention used by `tests/unit/api/__init__.py` etc.) and `tests/unit/core/test_api_keys.py`:

```python
"""Tests for core.api_keys: key generation and hashing."""

from regradar.core.api_keys import generate_api_key, hash_api_key


def test_generate_api_key_has_prefix():
    key = generate_api_key()
    assert key.startswith("rr_")


def test_generate_api_key_is_high_entropy_and_unique():
    keys = {generate_api_key() for _ in range(100)}
    assert len(keys) == 100


def test_generate_api_key_is_reasonably_long():
    key = generate_api_key()
    # "rr_" + urlsafe_b64(32 random bytes) is well over 40 chars
    assert len(key) > 40


def test_hash_api_key_is_deterministic():
    key = generate_api_key()
    assert hash_api_key(key) == hash_api_key(key)


def test_hash_api_key_differs_for_different_keys():
    assert hash_api_key(generate_api_key()) != hash_api_key(generate_api_key())


def test_hash_api_key_is_sha256_hex_digest():
    import hashlib

    key = "rr_known-test-value"
    expected = hashlib.sha256(key.encode()).hexdigest()
    assert hash_api_key(key) == expected


def test_hash_api_key_never_returns_the_raw_key():
    key = generate_api_key()
    assert hash_api_key(key) != key
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/core/test_api_keys.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.core.api_keys'`

- [ ] **Step 3: Write the implementation**

Create `src/regradar/core/api_keys.py`:

```python
"""Shared API key generation and hashing — used by both key issuance (`cli.py`)
and verification (`api/deps.py`) so the two can never drift out of sync.

API keys are high-entropy random strings, not low-entropy user passwords, so
they're hashed with a fast deterministic digest (SHA-256) rather than a slow
salted one (bcrypt/argon2) — the same choice Stripe and GitHub make for API
keys. This is what makes an indexed `WHERE key_hash = ?` lookup possible at
verification time; a salted hash would require iterating and checking every
active key's hash per request instead.
"""

import hashlib
import secrets

_KEY_PREFIX = "rr_"


def generate_api_key() -> str:
    """Generate a new, high-entropy, plaintext API key.

    The returned value is shown to the caller exactly once and is never
    stored — only its hash (see `hash_api_key`) is persisted.
    """
    return _KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(raw: str) -> str:
    """Deterministically hash a raw API key for storage/lookup."""
    return hashlib.sha256(raw.encode()).hexdigest()
```

Modify `src/regradar/core/config.py:117`:

```python
    api_key_hash_algorithm: str = Field(default="sha256", alias="API_KEY_HASH_ALGORITHM")
```

Modify `.env.example`, changing the `API_KEY_HASH_ALGORITHM=bcrypt` line to:

```
API_KEY_HASH_ALGORITHM=sha256
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/core/test_api_keys.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Lint and type-check**

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check src/regradar/core/api_keys.py tests/unit/core/`
Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/regradar/core/api_keys.py`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/regradar/core/api_keys.py src/regradar/core/config.py .env.example tests/unit/core/
git commit -m "Add API key generation and hashing (API-02 task 1/7)"
```

---

### Task 2: `role` column on `api_keys` (enum, model, migration)

**Files:**
- Modify: `src/regradar/models/enums.py` (add `ApiKeyRole`, after the `EvalRunType` class at line 66)
- Modify: `src/regradar/models/api_key.py` (add `role` column)
- Create: `migrations/versions/0007_add_api_key_role.py`

**Interfaces:**
- Produces: `ApiKeyRole(str, enum.Enum)` with members `ADMIN`, `ANALYST`, `EXECUTIVE`, `LEGAL_COUNSEL`, `ENG_LEAD` (values: `"admin"`, `"analyst"`, `"executive"`, `"legal_counsel"`, `"eng_lead"`) — consumed by Task 4 (`api/deps.py`'s `AuthenticatedKey.role` field) and Task 6 (`cli.py`'s `--role` argument)
- Consumes: `pg_enum_values` from `models/enums.py:6` (already defined)

This task has no dedicated pytest suite (the codebase has no migration test harness — acceptance is proven by running the migration against a real database, matching FOUND-02's own precedent). Steps below are the migration-verification equivalent of red/green.

- [ ] **Step 1: Add the enum**

Append to `src/regradar/models/enums.py` (after `EvalRunType`, i.e. after line 66):

```python


class ApiKeyRole(str, enum.Enum):
    ADMIN = "admin"
    ANALYST = "analyst"
    EXECUTIVE = "executive"
    LEGAL_COUNSEL = "legal_counsel"
    ENG_LEAD = "eng_lead"
```

- [ ] **Step 2: Add the column to the ORM model**

Modify `src/regradar/models/api_key.py`. Current content:

```python
"""ORM model for `api_keys` — authenticates API consumers."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from regradar.core.db import Base

if TYPE_CHECKING:
    from regradar.models.webhook import Webhook


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    owner_label: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    webhooks: Mapped[list["Webhook"]] = relationship(back_populates="api_key")
```

Replace with:

```python
"""ORM model for `api_keys` — authenticates API consumers."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum as SAEnum, Integer, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from regradar.core.db import Base
from regradar.models.enums import ApiKeyRole, pg_enum_values

if TYPE_CHECKING:
    from regradar.models.webhook import Webhook


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    owner_label: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[ApiKeyRole] = mapped_column(
        SAEnum(ApiKeyRole, name="api_key_role", values_callable=pg_enum_values),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    webhooks: Mapped[list["Webhook"]] = relationship(back_populates="api_key")
```

- [ ] **Step 3: Write the migration**

Create `migrations/versions/0007_add_api_key_role.py`:

```python
"""Add role to api_keys.

API-02 needs a role to resolve permissions per the Security & Access
Document's role model (Admin/Analyst/Executive/Legal Counsel/Eng Lead),
but FOUND-02's original api_keys table never included one. The table is
empty in every environment so far, so this adds the column NOT NULL with
no backfill needed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

ROLE_VALUES = ["admin", "analyst", "executive", "legal_counsel", "eng_lead"]


def upgrade() -> None:
    values_sql = ", ".join(f"'{v}'" for v in ROLE_VALUES)
    op.execute(f"CREATE TYPE api_key_role AS ENUM ({values_sql})")
    op.add_column(
        "api_keys",
        sa.Column(
            "role",
            postgresql.ENUM(*ROLE_VALUES, name="api_key_role", create_type=False),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "role")
    op.execute("DROP TYPE api_key_role")
```

- [ ] **Step 4: Run the migration against a real database and verify both directions**

Start a real Postgres (e.g. `docker start infra-postgres-1` if it exists from a prior ticket, or `docker compose -f infra/docker-compose.yml up -d postgres`), then:

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m alembic upgrade head`
Expected: succeeds, no errors

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -c "import asyncio; from sqlalchemy import text; from regradar.core.db import get_engine; asyncio.run((lambda: get_engine().connect())().__aenter__())"` — or more simply, verify via psql/`docker exec`:
Run: `docker exec infra-postgres-1 psql -U regradar -d regradar -c "\d api_keys"`
Expected: shows a `role` column of type `api_key_role`, `not null`

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m alembic downgrade -1`
Expected: succeeds, `role` column and `api_key_role` type both gone (verify with the same `\d api_keys` — column no longer listed)

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m alembic upgrade head` again
Expected: succeeds — leaves the database on `0007` for the next task, which needs the column to exist

- [ ] **Step 5: Lint and type-check**

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check src/regradar/models/`
Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/regradar/models/`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/regradar/models/enums.py src/regradar/models/api_key.py migrations/versions/0007_add_api_key_role.py
git commit -m "Add role column to api_keys (API-02 task 2/7)"
```

---

### Task 3: Shared error envelope (`api/errors.py`)

**Files:**
- Create: `src/regradar/api/errors.py`
- Modify: `src/regradar/api/main.py` (register the exception handler)
- Test: `tests/unit/api/test_errors.py`

**Interfaces:**
- Consumes: `request_id_ctx` from `src/regradar/api/middleware/request_id.py` (already exists, API-01)
- Produces: `ApiError(HTTPException)` with `__init__(self, status_code: int, code: str, message: str)`, consumed by Task 4 (`api/deps.py`) to signal auth failures

- [ ] **Step 1: Write the failing test**

Create `tests/unit/api/test_errors.py`:

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

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.api.errors'`

- [ ] **Step 3: Write the implementation**

Create `src/regradar/api/errors.py`:

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

Modify `src/regradar/api/main.py`: add the import and call `register_error_handlers(app)` inside `create_app()`, right after `app.add_middleware(RequestIdMiddleware)` (line 39):

```python
from regradar.api.errors import register_error_handlers
from regradar.api.middleware.request_id import RequestIdFilter, RequestIdMiddleware
```

```python
def create_app() -> FastAPI:
    app = FastAPI(title="RegRadar", version=version("regradar"), docs_url="/docs")
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_errors.py tests/unit/api/test_main.py -v`
Expected: PASS (all tests, including the pre-existing `test_main.py` suite — confirms the new handler registration doesn't break `/health`)

- [ ] **Step 5: Lint and type-check**

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check src/regradar/api/`
Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/regradar/api/`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/regradar/api/errors.py src/regradar/api/main.py tests/unit/api/test_errors.py
git commit -m "Add shared API error envelope (API-02 task 3/7)"
```

---

### Task 4: Auth dependency (`api/deps.py`)

**Files:**
- Create: `src/regradar/api/deps.py`
- Test: `tests/unit/api/test_deps.py`

**Interfaces:**
- Consumes: `hash_api_key` from `core/api_keys.py` (Task 1); `ApiKeyRole` from `models/enums.py` (Task 2); `ApiKey` from `models/api_key.py` (Task 2); `ApiError` from `api/errors.py` (Task 3); `get_session_factory` from `core/db.py` (existing)
- Produces: `AuthenticatedKey(BaseModel)` with fields `id: uuid.UUID`, `role: ApiKeyRole`, `owner_label: str`, `rate_limit_per_minute: int`; `async def get_current_key(authorization: str = Header(default="")) -> AuthenticatedKey` — consumed by Task 5 (`api/routers/whoami.py`)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/api/test_deps.py`. This follows the same DB-mocking pattern as `tests/unit/workers/test_pipeline_tasks.py` (mock `get_session_factory`, mock the session's `execute`/`commit`), since `get_current_key` isn't yet mounted on any route:

```python
"""Tests for the API key authentication dependency."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from regradar.api import deps as deps_module
from regradar.api.errors import ApiError
from regradar.core.api_keys import hash_api_key
from regradar.models.enums import ApiKeyRole


def _mock_row(*, is_active: bool = True, role: ApiKeyRole = ApiKeyRole.ADMIN):
    row = MagicMock()
    row.id = uuid4()
    row.role = role
    row.owner_label = "test-owner"
    row.rate_limit_per_minute = 60
    row.is_active = is_active
    return row


def _patch_db(monkeypatch: pytest.MonkeyPatch, *, found_row=None):
    mock_db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=found_row)
    mock_db.execute = AsyncMock(return_value=result)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(deps_module, "get_session_factory", lambda: mock_session_factory)
    return mock_db


async def test_missing_header_returns_401():
    with pytest.raises(ApiError) as exc_info:
        await deps_module.get_current_key(authorization="")

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "invalid_api_key"


async def test_malformed_header_returns_401():
    with pytest.raises(ApiError) as exc_info:
        await deps_module.get_current_key(authorization="not-bearer-format")

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "invalid_api_key"


async def test_unknown_key_returns_401(monkeypatch: pytest.MonkeyPatch):
    _patch_db(monkeypatch, found_row=None)

    with pytest.raises(ApiError) as exc_info:
        await deps_module.get_current_key(authorization="Bearer rr_unknown-key")

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "invalid_api_key"


async def test_revoked_key_returns_401(monkeypatch: pytest.MonkeyPatch):
    _patch_db(monkeypatch, found_row=_mock_row(is_active=False))

    with pytest.raises(ApiError) as exc_info:
        await deps_module.get_current_key(authorization="Bearer rr_revoked-key")

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "invalid_api_key"


async def test_valid_active_key_returns_authenticated_key(monkeypatch: pytest.MonkeyPatch):
    row = _mock_row(is_active=True, role=ApiKeyRole.ANALYST)
    _patch_db(monkeypatch, found_row=row)

    result = await deps_module.get_current_key(authorization="Bearer rr_valid-key")

    assert result.id == row.id
    assert result.role == ApiKeyRole.ANALYST
    assert result.owner_label == "test-owner"
    assert result.rate_limit_per_minute == 60


async def test_valid_key_updates_last_used_at(monkeypatch: pytest.MonkeyPatch):
    row = _mock_row()
    mock_db = _patch_db(monkeypatch, found_row=row)

    await deps_module.get_current_key(authorization="Bearer rr_valid-key")

    assert row.last_used_at is not None
    mock_db.commit.assert_awaited_once()


async def test_lookup_uses_hash_not_plaintext(monkeypatch: pytest.MonkeyPatch):
    """The presented key must never be compared/stored in plaintext."""
    row = _mock_row()
    mock_db = _patch_db(monkeypatch, found_row=row)

    await deps_module.get_current_key(authorization="Bearer rr_valid-key")

    executed_stmt = mock_db.execute.call_args[0][0]
    compiled = str(executed_stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "key_hash" in compiled
```

Note: `datetime`/`UTC`/`HTTPException` imports above are unused if the implementation doesn't need them in the test file directly — remove any import your editor/linter flags as unused once the implementation is written (ruff will catch this in Step 5).

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_deps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.api.deps'`

- [ ] **Step 3: Write the implementation**

Create `src/regradar/api/deps.py`:

```python
"""FastAPI dependencies shared across route handlers."""

import uuid
from datetime import UTC, datetime

from fastapi import Header
from pydantic import BaseModel
from sqlalchemy import select

from regradar.api.errors import ApiError
from regradar.core.api_keys import hash_api_key
from regradar.core.db import get_session_factory
from regradar.models.api_key import ApiKey
from regradar.models.enums import ApiKeyRole

_INVALID_KEY_ERROR = ApiError(
    status_code=401,
    code="invalid_api_key",
    message="Missing, malformed, unknown, or revoked API key.",
)


class AuthenticatedKey(BaseModel):
    id: uuid.UUID
    role: ApiKeyRole
    owner_label: str
    rate_limit_per_minute: int


async def get_current_key(authorization: str = Header(default="")) -> AuthenticatedKey:
    """Resolve the calling API key from the Authorization header.

    Raises ApiError(401) for a missing, malformed, unknown, or revoked key —
    deliberately the same error in every case, so a caller can't use the
    response to distinguish "this key doesn't exist" from "this key was
    revoked".
    """
    if not authorization.startswith("Bearer "):
        raise _INVALID_KEY_ERROR

    presented_key = authorization.removeprefix("Bearer ").strip()
    if not presented_key:
        raise _INVALID_KEY_ERROR

    key_hash = hash_api_key(presented_key)

    session_factory = get_session_factory()
    async with session_factory() as db:
        result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        row = result.scalar_one_or_none()

        if row is None or not row.is_active:
            raise _INVALID_KEY_ERROR

        row.last_used_at = datetime.now(UTC)
        await db.commit()

        return AuthenticatedKey(
            id=row.id,
            role=row.role,
            owner_label=row.owner_label,
            rate_limit_per_minute=row.rate_limit_per_minute,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_deps.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Lint and type-check, remove unused test imports**

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check src/regradar/api/deps.py tests/unit/api/test_deps.py`

If ruff flags unused imports in the test file (`datetime`, `UTC`, `HTTPException`), remove them from the import line at the top of `tests/unit/api/test_deps.py`.

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/regradar/api/deps.py`
Expected: both clean

- [ ] **Step 6: Re-run to confirm clean after import fix**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_deps.py -v`
Expected: PASS (7 passed)

- [ ] **Step 7: Commit**

```bash
git add src/regradar/api/deps.py tests/unit/api/test_deps.py
git commit -m "Add API key auth dependency (API-02 task 4/7)"
```

---

### Task 5: Throwaway authenticated route (`GET /v1/_whoami`)

**Files:**
- Create: `src/regradar/api/routers/whoami.py`
- Modify: `src/regradar/api/main.py` (mount the router)
- Test: `tests/unit/api/test_whoami_route.py`

**Interfaces:**
- Consumes: `get_current_key`, `AuthenticatedKey` from `api/deps.py` (Task 4)
- Produces: `router: APIRouter` — mounted in `create_app()`, gives Task 4's dependency a real HTTP entrypoint. Delete this file (and its `main.py` wiring) once API-04 exists and provides a real authenticated route.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/api/test_whoami_route.py`:

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

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_whoami_route.py -v`
Expected: FAIL — first test gets 404 (no `/v1/_whoami` route mounted yet), not 401

- [ ] **Step 3: Write the implementation**

Create `src/regradar/api/routers/whoami.py`:

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

Modify `src/regradar/api/main.py`: add the import and mount the router inside `create_app()`, right after `register_error_handlers(app)`:

```python
from regradar.api.routers.whoami import router as whoami_router
```

```python
def create_app() -> FastAPI:
    app = FastAPI(title="RegRadar", version=version("regradar"), docs_url="/docs")
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(whoami_router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/api/test_whoami_route.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full unit suite to confirm nothing else broke**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -q`
Expected: all pass (the pre-existing `test_main.py` health/docs/request-ID tests must still pass unchanged)

- [ ] **Step 6: Lint and type-check**

Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check src/regradar/api/`
Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/regradar/api/`
Expected: both clean

- [ ] **Step 7: Commit**

```bash
git add src/regradar/api/routers/whoami.py src/regradar/api/main.py tests/unit/api/test_whoami_route.py
git commit -m "Mount throwaway /v1/_whoami route to exercise auth over HTTP (API-02 task 5/7)"
```

---

### Task 6: `create-api-key` CLI command

**Files:**
- Modify: `src/regradar/cli.py`
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `generate_api_key`, `hash_api_key` from `core/api_keys.py` (Task 1); `ApiKey` from `models/api_key.py`, `ApiKeyRole` from `models/enums.py` (Task 2); `get_session_factory` from `core/db.py` (existing)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli.py`:

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

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/test_cli.py -v`
Expected: FAIL with `AttributeError: module 'regradar.cli' has no attribute '_create_api_key'`

- [ ] **Step 3: Write the implementation**

Modify `src/regradar/cli.py`. Current content is:

```python
"""Local dev entrypoints: run a single filing through the pipeline, run eval suites, etc."""

import argparse
import asyncio


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="regradar")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "poll-once", help="Run a single ingestion cycle across all active sources, then exit."
    )

    args = parser.parse_args()

    if args.command == "poll-once":
        _poll_once()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

Replace with:

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/test_cli.py -v`
Expected: PASS (3 passed)

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
git commit -m "Add create-api-key CLI command (API-02 task 6/7)"
```

---

### Task 7: Live verification against real infrastructure

**Files:** None (no code changes — this task proves the previous six tasks work against real infrastructure, matching every prior ticket's live-verification bar).

**Interfaces:** None — this is the final verification pass, not a new component.

- [ ] **Step 1: Start real Postgres**

Run: `docker start infra-postgres-1` (or `docker compose -f infra/docker-compose.yml up -d postgres` if that container doesn't exist)

Confirm migrations are current: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m alembic upgrade head`

- [ ] **Step 2: Mint a real key via the CLI**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m regradar.cli create-api-key --owner-label "live-verification" --role admin`

Expected: prints a real `rr_...` key. Copy it for the next step.

- [ ] **Step 3: Boot the real app and hit /v1/_whoami**

Run (background): `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m uvicorn regradar.api.main:app --port 8123`

Run: `curl -s -i http://127.0.0.1:8123/v1/_whoami` (no header)
Expected: `401`, JSON body `{"error": {"code": "invalid_api_key", ...}}`

Run: `curl -s -i http://127.0.0.1:8123/v1/_whoami -H "Authorization: Bearer <the real key from Step 2>"`
Expected: `200`, JSON body `{"role": "admin", "owner_label": "live-verification"}`

Run: `curl -s -i http://127.0.0.1:8123/v1/_whoami -H "Authorization: Bearer rr_this-key-does-not-exist"`
Expected: `401`, same envelope as the missing-header case

- [ ] **Step 4: Verify last_used_at actually updated in the real database**

Run: `docker exec infra-postgres-1 psql -U regradar -d regradar -c "SELECT owner_label, role, last_used_at FROM api_keys WHERE owner_label = 'live-verification';"`
Expected: a row with `role = admin` and a non-null, recent `last_used_at`

- [ ] **Step 5: Clean up**

Stop the uvicorn process (kill the background job from Step 3).

Delete the live-verification key from the database (matches the project's per-ticket cleanup convention — every prior ticket's live-verification data was removed after confirming it worked):

Run: `docker exec infra-postgres-1 psql -U regradar -d regradar -c "DELETE FROM api_keys WHERE owner_label = 'live-verification';"`

Stop Postgres if it wasn't already running for other work: `docker stop infra-postgres-1`

- [ ] **Step 6: Final full-suite check**

Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -q`
Run: `/Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m ruff check .`
Run: `PYTHONPATH=src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m mypy src/`
Expected: all three clean

- [ ] **Step 7: Push the branch**

```bash
git push -u origin worktree-api-02-key-auth
```

Report back to the user with a summary and ask before merging to `master` — do not merge without explicit go-ahead, per this project's established workflow (see `docs/superpowers/specs/2026-08-24-api-02-api-key-authentication-design.md` and prior tickets' merge pattern).
