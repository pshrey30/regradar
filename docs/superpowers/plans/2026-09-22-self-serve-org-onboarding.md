# Self-Serve Admin Signup & Organization Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a brand-new visitor create an organization and become its Admin without an invite, require that Admin to fill in `OrganizationProfile` during onboarding, and hard-block filing scoring for any organization until that profile is complete.

**Architecture:** A new `POST /v1/auth/signup-org` endpoint (parallel to the existing invite-gated `/v1/auth/signup`) creates an `Organization` + `ApiKey(role=ADMIN)` in one transaction. A new `GET/PUT /v1/organizations/me/profile` endpoint reads/writes `OrganizationProfile`, gated Admin-only for writes via `organization_profiles`' own RLS policies (no service-role workaround needed). `pipeline_tasks.py` checks profile completeness before running the LangGraph pipeline at all, parking incomplete-org filings at a new `FilingStatus.NEEDS_ORGANIZATION_SETUP`; saving a complete profile flips those filings back to `INGESTED` so the existing `process-pending` path picks them up normally. The frontend gates every route except `/onboarding` behind profile completeness, reusing `/v1/me`'s existing session-check round trip rather than adding a second one.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Alembic, Postgres RLS, React + TypeScript, Vitest/pytest.

## Global Constraints

- Self-serve org creation bypasses invites entirely — supersedes the existing `schemas/auth.py` docstring claiming "an Admin account is only ever created by another Admin issuing an Admin-role invite."
- Email verification is out of scope for this plan — deferred to a future change.
- The pipeline gate on missing/incomplete `OrganizationProfile` is a hard block: no scoring happens at all, not a soft default.
- All five `OrganizationProfile` fields (industry, business_description, watchlist_entities, products, risk_priorities) are required — enforced at the Pydantic schema level (422 before any DB write).
- `OrganizationProfile` writes are Admin-only, both via API-level check and DB-level RLS policy.
- Every DB-touching route in this plan uses `Depends(get_authenticated_db)`, never bare `get_db` (per `deps.py`'s existing convention) — `organization_profiles`' new RLS policies (Task 1) make this sufficient with no `service`-role re-assertion needed, unlike tables that stayed service-only.

---

### Task 1: Migration — `NEEDS_ORGANIZATION_SETUP` status, org-scoped `organization_profiles` RLS, and the `is_complete()` helper

**Files:**
- Create: `migrations/versions/0027_org_signup_and_setup_gate.py`
- Modify: `src/regradar/models/enums.py` (add `NEEDS_ORGANIZATION_SETUP` to `FilingStatus`)
- Modify: `src/regradar/models/organization_profile.py` (add `is_complete()` function)
- Test: `tests/unit/models/test_organization_profile.py`

**Interfaces:**
- Produces: `FilingStatus.NEEDS_ORGANIZATION_SETUP` (used by Task 4's pipeline gate and Task 3's requeue).
- Produces: `organization_profile.is_complete(profile: OrganizationProfile | None) -> bool` (used by Task 3's API responses, Task 4's pipeline gate, and Task 5's `/v1/me`).
- Produces: `organization_profiles` RLS policies that allow the caller's own resolved role (SELECT for any authenticated role scoped to their org; INSERT/UPDATE for Admin scoped to their org, or service) — no code outside this migration needs to know the exact policy text, only that `Depends(get_authenticated_db)` is now sufficient to read/write this table for an Admin's own org.

- [ ] **Step 1: Add `NEEDS_ORGANIZATION_SETUP` to `FilingStatus`**

Edit `src/regradar/models/enums.py`:

```python
class FilingStatus(str, enum.Enum):
    INGESTED = "ingested"
    CLASSIFYING = "classifying"
    NEEDS_CLASSIFICATION = "needs_classification"
    NEEDS_REVIEW = "needs_review"
    NEEDS_ORGANIZATION_SETUP = "needs_organization_setup"
    RETRIEVING = "retrieving"
    ANALYZING = "analyzing"
    SUMMARIZING = "summarizing"
    DELIVERING = "delivering"
    COMPLETE = "complete"
    FAILED = "failed"
```

- [ ] **Step 2: Write the failing test for `is_complete()`**

Create `tests/unit/models/test_organization_profile.py`:

```python
"""Unit tests for OrganizationProfile.is_complete()."""

import uuid

from regradar.models.organization_profile import OrganizationProfile, is_complete


def _complete_profile() -> OrganizationProfile:
    return OrganizationProfile(
        organization_id=uuid.uuid4(),
        industry="Biotechnology",
        business_description="We manufacture diagnostic devices.",
        watchlist_entities=["Acme Corp"],
        products=["Widget Pro"],
        risk_priorities=["Data privacy"],
    )


def test_is_complete_returns_false_for_none() -> None:
    assert is_complete(None) is False


def test_is_complete_returns_true_when_all_fields_set() -> None:
    assert is_complete(_complete_profile()) is True


def test_is_complete_returns_false_when_industry_missing() -> None:
    profile = _complete_profile()
    profile.industry = None
    assert is_complete(profile) is False


def test_is_complete_returns_false_when_business_description_missing() -> None:
    profile = _complete_profile()
    profile.business_description = None
    assert is_complete(profile) is False


def test_is_complete_returns_false_when_watchlist_entities_empty() -> None:
    profile = _complete_profile()
    profile.watchlist_entities = []
    assert is_complete(profile) is False


def test_is_complete_returns_false_when_products_empty() -> None:
    profile = _complete_profile()
    profile.products = []
    assert is_complete(profile) is False


def test_is_complete_returns_false_when_risk_priorities_empty() -> None:
    profile = _complete_profile()
    profile.risk_priorities = []
    assert is_complete(profile) is False
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/unit/models/test_organization_profile.py -v`
Expected: FAIL with `ImportError: cannot import name 'is_complete'`

- [ ] **Step 4: Implement `is_complete()`**

Edit `src/regradar/models/organization_profile.py`, appending after the class:

```python
def is_complete(profile: "OrganizationProfile | None") -> bool:
    """Whether every field this project requires at onboarding is
    actually populated — the single predicate the onboarding-redirect
    check (API-11), the pipeline gate (API-11), and /v1/me (API-11) all
    share, so they can't silently drift apart on what "complete" means."""
    if profile is None:
        return False
    return bool(
        profile.industry
        and profile.business_description
        and profile.watchlist_entities
        and profile.products
        and profile.risk_priorities
    )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/models/test_organization_profile.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Write the migration**

Create `migrations/versions/0027_org_signup_and_setup_gate.py`:

```python
"""API-11 — self-serve Admin signup & organization onboarding.

Adds FilingStatus.NEEDS_ORGANIZATION_SETUP (0026's filing_status enum gets
a new value the same way 0023 added `engineering` to filing_domain).

Also fixes organization_profiles' RLS policy (0024): it was created
service-role-only ("no admin-facing API to manage this exists yet" — 0024's
own docstring), but this migration is exactly that API. Replaces the
single ALL/service policy with the same shape 0009/0010 already established
for source_configs: SELECT for any authenticated role scoped to their own
org, INSERT/UPDATE for Admin (scoped to their own org) or service. No
DELETE policy — nothing in this app ever deletes a profile.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-22
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

_NEW_STATUS = "needs_organization_setup"
_ORIGINAL_STATUSES = [
    "ingested",
    "classifying",
    "needs_classification",
    "needs_review",
    "retrieving",
    "analyzing",
    "summarizing",
    "delivering",
    "complete",
    "failed",
]

_IS_SERVICE = "current_setting('app.current_role', true) = 'service'"
_IS_ADMIN = "current_setting('app.current_role', true) = 'admin'"
_AUTHENTICATED = (
    "current_setting('app.current_role', true) IN "
    "('admin', 'analyst', 'executive', 'legal_counsel', 'eng_lead')"
)
_ORG_MATCH = "organization_id::text = current_setting('app.current_organization_id', true)"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE filing_status ADD VALUE '{_NEW_STATUS}'")

    op.execute("DROP POLICY organization_profiles_service ON organization_profiles")
    op.execute(
        f"CREATE POLICY organization_profiles_select ON organization_profiles FOR SELECT "
        f"USING ({_AUTHENTICATED} AND {_ORG_MATCH})"
    )
    write_check = f"(({_IS_ADMIN} AND {_ORG_MATCH}) OR {_IS_SERVICE})"
    op.execute(
        f"CREATE POLICY organization_profiles_insert ON organization_profiles FOR INSERT "
        f"WITH CHECK {write_check}"
    )
    op.execute(
        f"CREATE POLICY organization_profiles_update ON organization_profiles FOR UPDATE "
        f"USING {write_check}"
    )


def downgrade() -> None:
    op.execute("DROP POLICY organization_profiles_select ON organization_profiles")
    op.execute("DROP POLICY organization_profiles_insert ON organization_profiles")
    op.execute("DROP POLICY organization_profiles_update ON organization_profiles")
    op.execute(
        f"CREATE POLICY organization_profiles_service ON organization_profiles "
        f"FOR ALL USING ({_IS_SERVICE}) WITH CHECK ({_IS_SERVICE})"
    )

    op.execute(
        f"UPDATE filings SET status = 'needs_review' WHERE status = '{_NEW_STATUS}'"
    )
    values_sql = ", ".join(f"'{v}'" for v in _ORIGINAL_STATUSES)
    op.execute(f"CREATE TYPE filing_status_old AS ENUM ({values_sql})")
    op.execute(
        "ALTER TABLE filings ALTER COLUMN status TYPE filing_status_old "
        "USING status::text::filing_status_old"
    )
    op.execute("DROP TYPE filing_status")
    op.execute("ALTER TYPE filing_status_old RENAME TO filing_status")
```

- [ ] **Step 7: Commit**

```bash
git add migrations/versions/0027_org_signup_and_setup_gate.py src/regradar/models/enums.py src/regradar/models/organization_profile.py tests/unit/models/test_organization_profile.py
git commit -m "feat: add NEEDS_ORGANIZATION_SETUP status and org-scoped organization_profiles RLS"
```

---

### Task 2: Backend — `POST /v1/auth/signup-org`

**Files:**
- Modify: `src/regradar/schemas/auth.py`
- Modify: `src/regradar/api/routers/auth.py`
- Test: `tests/unit/api/test_auth_password_route.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `SignupOrgRequest{org_name: str, display_name: str, email: str, password: str}` (schema) and `POST /v1/auth/signup-org` (route, 201 on success, 409 `email_taken`, 422 `weak_password` or validation error) — Task 6 (frontend) posts to this.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/api/test_auth_password_route.py` (same file — it already imports `auth as auth_module`, `ApiError`, `hash_password`, `ApiKeyRole`):

```python
from regradar.models.organization import Organization
from regradar.schemas.auth import SignupOrgRequest


def _patch_signup_org_db(
    monkeypatch: pytest.MonkeyPatch, *, existing_email_row=None
):
    """Sequences signup_org()'s one query (email-uniqueness check) — no
    invite consumption, no org lookup (a NEW org is created, never
    looked up)."""
    mock_db = AsyncMock()

    email_result = MagicMock()
    email_result.scalar_one_or_none = MagicMock(return_value=existing_email_row)
    mock_db.execute = AsyncMock(return_value=email_result)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(auth_module, "get_session_factory", lambda: mock_session_factory)
    return mock_db


@pytest.mark.asyncio
async def test_signup_org_creates_organization_and_admin_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_db = _patch_signup_org_db(monkeypatch)

    response = await auth_module.signup_org(
        SignupOrgRequest(
            org_name="Acme Corp",
            display_name="Jane Admin",
            email="jane@acme.example",
            password="correct horse battery staple",
        )
    )

    assert response.status_code == 201
    added_rows = [call.args[0] for call in mock_db.add.call_args_list]
    orgs = [row for row in added_rows if isinstance(row, Organization)]
    keys = [row for row in added_rows if isinstance(row, ApiKey)]
    assert len(orgs) == 1
    assert orgs[0].name == "Acme Corp"
    assert len(keys) == 1
    assert keys[0].role == ApiKeyRole.ADMIN
    assert keys[0].email == "jane@acme.example"
    assert keys[0].owner_label == "Jane Admin"
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_signup_org_rejects_duplicate_email(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = MagicMock()
    _patch_signup_org_db(monkeypatch, existing_email_row=existing)

    with pytest.raises(ApiError) as exc_info:
        await auth_module.signup_org(
            SignupOrgRequest(
                org_name="Acme Corp",
                display_name="Jane Admin",
                email="jane@acme.example",
                password="correct horse battery staple",
            )
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "email_taken"


@pytest.mark.asyncio
async def test_signup_org_rejects_weak_password(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_signup_org_db(monkeypatch)

    with pytest.raises(ApiError) as exc_info:
        await auth_module.signup_org(
            SignupOrgRequest(
                org_name="Acme Corp",
                display_name="Jane Admin",
                email="jane@acme.example",
                password="weak",
            )
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "weak_password"


def test_signup_org_request_rejects_empty_org_name() -> None:
    with pytest.raises(ValueError):
        SignupOrgRequest(
            org_name="",
            display_name="Jane Admin",
            email="jane@acme.example",
            password="correct horse battery staple",
        )
```

(Check `tests/unit/api/test_auth_password_route.py`'s existing imports for `pytest` and confirm whether the file already has `@pytest.mark.asyncio` tests and an `asyncio_mode` config — if `pyproject.toml`'s `[tool.pytest.ini_options]` sets `asyncio_mode = "auto"`, drop the explicit `@pytest.mark.asyncio` decorators to match the file's existing style.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_auth_password_route.py -k signup_org -v`
Expected: FAIL with `ImportError: cannot import name 'SignupOrgRequest'` / `AttributeError: module 'auth' has no attribute 'signup_org'`

- [ ] **Step 3: Add `SignupOrgRequest` and extract the shared email validator**

Edit `src/regradar/schemas/auth.py` — replace the `SignupRequest` class's inline validator with a shared function, then add the new schema:

```python
from pydantic import BaseModel, Field, field_validator
```

```python
def _normalize_and_validate_email(value: str) -> str:
    if not _EMAIL_PATTERN.match(value):
        raise ValueError("Not a valid email address")
    return value.lower()


class SignupRequest(BaseModel):
    email: str
    password: str
    invite_code: str
    role: ApiKeyRole
    display_name: str | None = None

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        return _normalize_and_validate_email(value)

    @field_validator("role")
    @classmethod
    def _validate_self_selectable_role(cls, value: ApiKeyRole) -> ApiKeyRole:
        if value not in SELF_SELECTABLE_ROLES:
            raise ValueError(
                "Not a role you can sign up as — an Admin account is granted by "
                "another Admin, never chosen at signup."
            )
        return value


class SignupOrgRequest(BaseModel):
    """The no-invite counterpart to SignupRequest: creates a brand-new
    Organization and its first Admin account together. No `role` or
    `invite_code` field — both are implied (Admin, no invite required)."""

    org_name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        return _normalize_and_validate_email(value)
```

Also update the `SELF_SELECTABLE_ROLES` comment (it currently ends "an Admin account is only ever created by another Admin issuing an Admin-role invite," which is no longer true):

```python
# Every role a signing-up person may choose for themselves — deliberately
# excludes Admin. Self-service role escalation is never allowed anywhere
# in this app (see api/routers/api_keys.py's PATCH /v1/api-keys/{id} for
# the one place roles *can* change, which is Admin-only and never the
# caller's own choice); an Admin account is created either by another
# Admin issuing an Admin-role invite into an EXISTING org, or by
# POST /v1/auth/signup-org, which creates a brand-new org with no invite
# at all (see auth.py's signup_org()).
```

- [ ] **Step 4: Add the `signup_org` route**

Edit `src/regradar/api/routers/auth.py`. Update the import:

```python
from regradar.schemas.auth import (
    SELF_SELECTABLE_ROLES,
    ChangePasswordRequest,
    LoginRequest,
    SignupOrgRequest,
    SignupRequest,
)
```

Add the route immediately after `signup()` (after line 440's `return JSONResponse(...)`):

```python
@router.post("/v1/auth/signup-org", status_code=201)
async def signup_org(payload: SignupOrgRequest) -> Response:
    """The self-serve counterpart to signup(): creates a brand-new
    Organization and its first Admin account together, in one
    transaction, with no invite consumed or required. An invite only
    ever grants a non-Admin role into an EXISTING org (SELF_SELECTABLE_ROLES
    excludes Admin entirely) — this is the entry point that invites don't
    cover: becoming the Admin of a NEW org. Deliberately does not
    establish a session, matching signup()'s own "create only, then log
    in separately" convention.
    """
    try:
        password_hash = hash_password(payload.password)
    except WeakPasswordError as exc:
        raise ApiError(status_code=422, code="weak_password", message=str(exc)) from exc

    session_factory = get_session_factory()
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        existing = await db.execute(select(ApiKey.id).where(ApiKey.email == payload.email))
        if existing.scalar_one_or_none() is not None:
            raise ApiError(
                status_code=409,
                code="email_taken",
                message="An account with this email already exists.",
            )

        organization = Organization(name=payload.org_name)
        db.add(organization)
        await db.flush()  # populates organization.id before the ApiKey below needs it as a FK

        key = ApiKey(
            organization_id=organization.id,
            owner_label=payload.display_name,
            role=ApiKeyRole.ADMIN,
            email=payload.email,
            password_hash=password_hash,
            # Same "never meant to authenticate with this value" pattern
            # signup() already uses — login() rotates it to a real session
            # token on the first actual sign-in.
            key_hash=hash_api_key(generate_api_key()),
        )
        db.add(key)
        await db.commit()

    return JSONResponse(status_code=201, content={"status": "ok"})
```

Also update the module docstring's line 27-29 ("an actual Admin account is only ever created by another Admin directly (`create-api-key`), never through self-service signup") to add: "...or through `POST /v1/auth/signup-org`, which creates a brand-new organization with no invite at all."

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_auth_password_route.py -v`
Expected: PASS (all tests, including the existing ones — unaffected by the refactor)

- [ ] **Step 6: Run mypy and ruff**

Run: `uv run mypy src/regradar/schemas/auth.py src/regradar/api/routers/auth.py && uv run ruff check src/regradar/schemas/auth.py src/regradar/api/routers/auth.py tests/unit/api/test_auth_password_route.py`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add src/regradar/schemas/auth.py src/regradar/api/routers/auth.py tests/unit/api/test_auth_password_route.py
git commit -m "feat: add POST /v1/auth/signup-org for self-serve Admin org creation"
```

---

### Task 3: Backend — `GET/PUT /v1/organizations/me/profile`

**Files:**
- Create: `src/regradar/schemas/organization.py`
- Create: `src/regradar/api/routers/organizations.py`
- Modify: `src/regradar/api/main.py` (register the new router)
- Test: `tests/unit/api/test_organizations_route.py`

**Interfaces:**
- Consumes: `organization_profile.is_complete()` (Task 1), `organization_profiles` RLS policies allowing `Depends(get_authenticated_db)` direct access (Task 1).
- Produces: `OrganizationProfileResponse{industry, business_description, watchlist_entities, products, risk_priorities, is_complete}` and `OrganizationProfileRequest{industry, business_description, watchlist_entities, products, risk_priorities}` (schemas) — Task 7 (frontend) reads/writes these exact field names. `GET /v1/organizations/me/profile` (any role, 200 always, empty fields if no row) and `PUT /v1/organizations/me/profile` (Admin-only, 403 otherwise, 422 if any field missing/empty) — Task 4 relies on this PUT's requeue side effect.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/api/test_organizations_route.py`, mirroring `test_me_route.py`'s `TestClient` + `monkeypatch` convention:

```python
"""Tests for GET/PUT /v1/organizations/me/profile."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.models.enums import ApiKeyRole
from regradar.models.organization_profile import OrganizationProfile


def _authenticated_key_row(role: ApiKeyRole, organization_id: uuid.UUID):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.organization_id = organization_id
    row.role = role
    row.owner_label = "Test User"
    row.rate_limit_per_minute = 1000
    row.is_active = True
    row.email = "test@example.com"
    row.password_hash = None
    return row


def _mock_auth_and_rate_limit(monkeypatch: pytest.MonkeyPatch, *, role: ApiKeyRole, organization_id: uuid.UUID):
    row = _authenticated_key_row(role, organization_id)

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

    return row


def _mock_route_db(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_db = AsyncMock()
    monkeypatch.setattr(rate_limit_module, "get_db", lambda: iter([mock_db]))
    return mock_db


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_get_profile_returns_empty_fields_when_no_row_exists(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST, organization_id=org_id)
    mock_db = _mock_route_db(monkeypatch)
    mock_db.get = AsyncMock(return_value=None)

    response = client.get("/v1/organizations/me/profile", headers={"Authorization": "Bearer test"})

    assert response.status_code == 200
    body = response.json()
    assert body["industry"] is None
    assert body["watchlist_entities"] == []
    assert body["is_complete"] is False


def test_put_profile_rejects_non_admin(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ANALYST, organization_id=org_id)
    _mock_route_db(monkeypatch)

    response = client.put(
        "/v1/organizations/me/profile",
        headers={"Authorization": "Bearer test"},
        json={
            "industry": "Biotech",
            "business_description": "We make devices.",
            "watchlist_entities": ["Acme"],
            "products": ["Widget"],
            "risk_priorities": ["Privacy"],
        },
    )

    assert response.status_code == 403


def test_put_profile_rejects_missing_field(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN, organization_id=org_id)
    _mock_route_db(monkeypatch)

    response = client.put(
        "/v1/organizations/me/profile",
        headers={"Authorization": "Bearer test"},
        json={
            "industry": "Biotech",
            "business_description": "We make devices.",
            "watchlist_entities": [],
            "products": ["Widget"],
            "risk_priorities": ["Privacy"],
        },
    )

    assert response.status_code == 422


def test_put_profile_saves_and_requeues_parked_filings(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN, organization_id=org_id)
    mock_db = _mock_route_db(monkeypatch)
    mock_db.get = AsyncMock(return_value=None)
    mock_db.add = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()

    response = client.put(
        "/v1/organizations/me/profile",
        headers={"Authorization": "Bearer test"},
        json={
            "industry": "Biotech",
            "business_description": "We make devices.",
            "watchlist_entities": ["Acme"],
            "products": ["Widget"],
            "risk_priorities": ["Privacy"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_complete"] is True
    added = mock_db.add.call_args_list[0].args[0]
    assert isinstance(added, OrganizationProfile)
    assert added.industry == "Biotech"
    mock_db.execute.assert_awaited()  # the requeue UPDATE
    mock_db.commit.assert_awaited()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/api/test_organizations_route.py -v`
Expected: FAIL with 404s (route doesn't exist yet)

- [ ] **Step 3: Write the schemas**

Create `src/regradar/schemas/organization.py`:

```python
"""Request/response models for GET/PUT /v1/organizations/me/profile."""

from pydantic import BaseModel, Field


class OrganizationProfileResponse(BaseModel):
    industry: str | None
    business_description: str | None
    watchlist_entities: list[str]
    products: list[str]
    risk_priorities: list[str]
    is_complete: bool


class OrganizationProfileRequest(BaseModel):
    """All five fields are required — Field(min_length=1) rejects both a
    missing field and an empty string/list, matching the onboarding
    design's "all fields required" decision at the schema level, before
    any DB write."""

    industry: str = Field(min_length=1)
    business_description: str = Field(min_length=1)
    watchlist_entities: list[str] = Field(min_length=1)
    products: list[str] = Field(min_length=1)
    risk_priorities: list[str] = Field(min_length=1)
```

- [ ] **Step 4: Write the router**

Create `src/regradar/api/routers/organizations.py`:

```python
"""GET/PUT /v1/organizations/me/profile — the org's OrganizationProfile
(industry, business description, watchlist entities, products, risk
priorities) that relevance_agent.py uses to score how much a filing
matters to *this* organization's business, not just how objectively
severe it is.

GET is readable by any authenticated role (matches organization_profiles'
RLS SELECT policy — migrations/versions/0027). PUT is Admin-only: this is
the org's shared business context, not a per-user setting.

0027's RLS policies grant the caller's own resolved role here directly —
Depends(get_authenticated_db) is sufficient, unlike tables whose policies
are still service-role-only.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.deps import AuthenticatedKey
from regradar.api.errors import ApiError
from regradar.api.middleware.rate_limit import enforce_rate_limit, get_authenticated_db
from regradar.models.enums import ApiKeyRole, FilingStatus
from regradar.models.filing import Filing
from regradar.models.organization_profile import OrganizationProfile, is_complete
from regradar.schemas.organization import OrganizationProfileRequest, OrganizationProfileResponse

router = APIRouter()

_ADMIN_ONLY_ERROR = ApiError(
    status_code=403,
    code="forbidden",
    message="Only the Admin role can update the organization profile.",
)


def _to_response(profile: OrganizationProfile | None) -> OrganizationProfileResponse:
    return OrganizationProfileResponse(
        industry=profile.industry if profile else None,
        business_description=profile.business_description if profile else None,
        watchlist_entities=list(profile.watchlist_entities) if profile else [],
        products=list(profile.products) if profile else [],
        risk_priorities=list(profile.risk_priorities) if profile else [],
        is_complete=is_complete(profile),
    )


@router.get("/v1/organizations/me/profile", response_model=OrganizationProfileResponse)
async def get_organization_profile(
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> OrganizationProfileResponse:
    profile = await db.get(OrganizationProfile, key.organization_id)
    return _to_response(profile)


@router.put("/v1/organizations/me/profile", response_model=OrganizationProfileResponse)
async def update_organization_profile(
    body: OrganizationProfileRequest,
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> OrganizationProfileResponse:
    if key.role != ApiKeyRole.ADMIN:
        raise _ADMIN_ONLY_ERROR

    profile = await db.get(OrganizationProfile, key.organization_id)
    if profile is None:
        profile = OrganizationProfile(organization_id=key.organization_id)
        db.add(profile)
    profile.industry = body.industry
    profile.business_description = body.business_description
    profile.watchlist_entities = body.watchlist_entities
    profile.products = body.products
    profile.risk_priorities = body.risk_priorities

    # Every filing parked waiting on this organization's setup gets the
    # same status flip fresh ingestion already uses (INGESTED) — the
    # existing `process-pending` path picks these up the normal way,
    # no separate re-trigger mechanism needed.
    await db.execute(
        update(Filing)
        .where(
            Filing.organization_id == key.organization_id,
            Filing.status == FilingStatus.NEEDS_ORGANIZATION_SETUP,
        )
        .values(status=FilingStatus.INGESTED)
    )
    await db.commit()

    return _to_response(profile)
```

- [ ] **Step 5: Register the router**

Edit `src/regradar/api/main.py`:

```python
from regradar.api.routers.organizations import router as organizations_router
```

Add alongside the other `include_router` calls:

```python
app.include_router(organizations_router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_organizations_route.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Run mypy and ruff**

Run: `uv run mypy src/regradar/schemas/organization.py src/regradar/api/routers/organizations.py src/regradar/api/main.py && uv run ruff check src/regradar/schemas/organization.py src/regradar/api/routers/organizations.py src/regradar/api/main.py tests/unit/api/test_organizations_route.py`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add src/regradar/schemas/organization.py src/regradar/api/routers/organizations.py src/regradar/api/main.py tests/unit/api/test_organizations_route.py
git commit -m "feat: add GET/PUT /v1/organizations/me/profile"
```

---

### Task 4: Backend — pipeline gate on incomplete `OrganizationProfile`

**Files:**
- Modify: `src/regradar/workers/pipeline_tasks.py`
- Modify: `tests/unit/workers/test_pipeline_tasks.py`

**Interfaces:**
- Consumes: `organization_profile.is_complete()` (Task 1), `FilingStatus.NEEDS_ORGANIZATION_SETUP` (Task 1).
- Produces: no new public interface — `_run_pipeline_for_filing` now short-circuits before `build_graph().ainvoke(...)` for any filing whose org has no complete profile.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/workers/test_pipeline_tasks.py`:

```python
from regradar.models.organization_profile import OrganizationProfile


def _complete_profile_row(organization_id: uuid.UUID) -> MagicMock:
    row = MagicMock(spec=OrganizationProfile)
    row.organization_id = organization_id
    row.industry = "Biotechnology"
    row.business_description = "We manufacture diagnostic devices."
    row.watchlist_entities = ["Acme Corp"]
    row.products = ["Widget Pro"]
    row.risk_priorities = ["Data privacy"]
    return row


def test_process_filing_parks_filing_needing_organization_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No OrganizationProfile row at all for this filing's org — the
    pipeline must never run (zero LLM spend), and the filing is parked at
    NEEDS_ORGANIZATION_SETUP instead."""
    filing_id = uuid.uuid4()
    org_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.organization_id = org_id
    filing.raw_pdf_s3_key = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(
        side_effect=lambda model, *args, **kwargs: filing if model is Filing else None
    )
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory)
    build_graph_mock = MagicMock()
    monkeypatch.setattr(pipeline_tasks_module, "build_graph", build_graph_mock)

    process_filing.run(str(filing_id))

    assert filing.status == FilingStatus.NEEDS_ORGANIZATION_SETUP
    mock_db.commit.assert_awaited_once()
    build_graph_mock.assert_not_called()


def test_process_filing_parks_filing_with_incomplete_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A profile row exists but is missing required fields — still gated,
    same as no row at all."""
    filing_id = uuid.uuid4()
    org_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.organization_id = org_id
    filing.raw_pdf_s3_key = None

    incomplete_profile = _complete_profile_row(org_id)
    incomplete_profile.watchlist_entities = []  # the one field that's missing

    mock_db = AsyncMock()

    def _get(model, *args, **kwargs):
        if model is Filing:
            return filing
        return incomplete_profile

    mock_db.get = AsyncMock(side_effect=_get)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory)
    build_graph_mock = MagicMock()
    monkeypatch.setattr(pipeline_tasks_module, "build_graph", build_graph_mock)

    process_filing.run(str(filing_id))

    assert filing.status == FilingStatus.NEEDS_ORGANIZATION_SETUP
    build_graph_mock.assert_not_called()
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/unit/workers/test_pipeline_tasks.py -k organization_setup -v`
Expected: FAIL — `build_graph_mock.assert_not_called()` fails because the pipeline currently always runs

- [ ] **Step 3: Add the gate**

Edit `src/regradar/workers/pipeline_tasks.py`. Add the import:

```python
from regradar.models.organization_profile import OrganizationProfile, is_complete
```

Restructure `_run_pipeline_for_filing` — move the `OrganizationProfile` fetch to immediately after the filing-null-check (before PDF extraction, so a gated filing costs nothing, not even a PDF fetch), gate there, and remove the later duplicate fetch:

```python
async def _run_pipeline_for_filing(filing_id: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        filing = await db.get(Filing, uuid.UUID(filing_id))
        if filing is None:
            logger.warning("Filing %s not found — skipping pipeline run", filing_id)
            return

        profile_row = await db.get(OrganizationProfile, filing.organization_id)
        if not is_complete(profile_row):
            filing.status = FilingStatus.NEEDS_ORGANIZATION_SETUP
            await db.commit()
            return

        raw_text = ""
        chunks: list = []
        if filing.raw_pdf_s3_key:
            try:
                document_bytes = fetch_document_bytes(filing.raw_pdf_s3_key)
                raw_text, tables = extract_text_and_tables(document_bytes)
                if raw_text:
                    chunks = chunk_filing(raw_text, tables)
            except Exception as exc:  # noqa: BLE001 — never crash the pipeline over a bad/missing PDF
                logger.warning("PDF extraction failed for filing %s: %s", filing_id, exc)

        # profile_row is guaranteed complete here (is_complete() above
        # already confirmed it) — no `if profile_row is not None else
        # None` branch needed, unlike before this gate existed.
        org_profile = OrgProfileSnapshot(
            industry=profile_row.industry,
            business_description=profile_row.business_description,
            watchlist_entities=list(profile_row.watchlist_entities),
            products=list(profile_row.products),
            risk_priorities=list(profile_row.risk_priorities),
        )

        state = PipelineState(
            filing_id=filing.id, raw_text=raw_text, chunks=chunks or None, org_profile=org_profile
        )
        result = await build_graph().ainvoke(state, config={"configurable": {"db": db}})
```

Leave everything from the `# Real bug found live` comment onward unchanged.

- [ ] **Step 4: Fix the existing tests that now need a complete profile**

Every existing test in `tests/unit/workers/test_pipeline_tasks.py` that calls `process_filing.run(...)` and expects the pipeline to actually run (i.e. asserts on `filing.domain`, `filing.status == CLASSIFYING/COMPLETE/NEEDS_REVIEW`, or that `build_graph` was called) currently relies on `mock_db.get`'s `side_effect` returning `None` for any non-`Filing` model — that now hits the new gate instead of running the pipeline. Update each such test's `mock_db.get` side_effect to also return a complete profile for `OrganizationProfile`, e.g.:

```python
    org_id = uuid.uuid4()
    filing.organization_id = org_id
    profile = _complete_profile_row(org_id)
    mock_db.get = AsyncMock(
        side_effect=lambda model, *args, **kwargs: (
            filing if model is Filing else profile
        )
    )
```

Apply this to `test_process_filing_persists_classification_on_success` and `test_process_filing_reasserts_rls_role_after_graph_invoke` (both shown in the file already), and any other existing test in this file that reaches `build_graph().ainvoke(...)`.

- [ ] **Step 5: Run the full file to verify everything passes**

Run: `uv run pytest tests/unit/workers/test_pipeline_tasks.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 6: Run mypy and ruff**

Run: `uv run mypy src/regradar/workers/pipeline_tasks.py && uv run ruff check src/regradar/workers/pipeline_tasks.py tests/unit/workers/test_pipeline_tasks.py`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add src/regradar/workers/pipeline_tasks.py tests/unit/workers/test_pipeline_tasks.py
git commit -m "feat: gate pipeline processing on complete OrganizationProfile"
```

---

### Task 5: Backend — surface `organization_setup_complete` on `/v1/me`

**Files:**
- Modify: `src/regradar/schemas/me.py`
- Modify: `src/regradar/api/routers/me.py`
- Modify: `tests/unit/api/test_me_route.py`

**Interfaces:**
- Consumes: `organization_profile.is_complete()` (Task 1).
- Produces: `MeResponse.organization_setup_complete: bool` — Task 7 (frontend `AuthContext`) reads this field directly off the existing `/v1/me` response, no second request needed.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/api/test_me_route.py` (check its existing `_mock_route_db` helper — extend it or add a variant that also stubs `mock_db.get` for `OrganizationProfile`):

```python
from regradar.models.organization_profile import OrganizationProfile


def test_get_me_reports_organization_setup_incomplete_when_no_profile(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN, owner_label="Jane", organization_id=org_id)
    mock_db = _mock_route_db(monkeypatch)
    mock_db.get = AsyncMock(return_value=None)

    response = client.get("/v1/me", headers={"Authorization": "Bearer test"})

    assert response.status_code == 200
    assert response.json()["organization_setup_complete"] is False


def test_get_me_reports_organization_setup_complete_when_profile_filled(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    org_id = uuid.uuid4()
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN, owner_label="Jane", organization_id=org_id)
    mock_db = _mock_route_db(monkeypatch)
    profile = MagicMock(spec=OrganizationProfile)
    profile.industry = "Biotech"
    profile.business_description = "We make devices."
    profile.watchlist_entities = ["Acme"]
    profile.products = ["Widget"]
    profile.risk_priorities = ["Privacy"]
    mock_db.get = AsyncMock(return_value=profile)

    response = client.get("/v1/me", headers={"Authorization": "Bearer test"})

    assert response.status_code == 200
    assert response.json()["organization_setup_complete"] is True
```

(Match whichever exact helper names `test_me_route.py` already uses for `_mock_auth_and_rate_limit`/`_mock_route_db` — reuse them, don't duplicate.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/api/test_me_route.py -k organization_setup -v`
Expected: FAIL with `KeyError: 'organization_setup_complete'`

- [ ] **Step 3: Add the field to the schema**

Edit `src/regradar/schemas/me.py`:

```python
class MeResponse(BaseModel):
    role: ApiKeyRole
    organization_id: str | None
    display_name: str
    email: str | None = None
    has_password: bool = False
    organization_setup_complete: bool = False
```

- [ ] **Step 4: Compute it in both routes**

Edit `src/regradar/api/routers/me.py`:

```python
from regradar.models.organization_profile import OrganizationProfile, is_complete


@router.get("/v1/me", response_model=MeResponse)
async def get_me(
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> MeResponse:
    profile = await db.get(OrganizationProfile, key.organization_id)
    return MeResponse(
        role=key.role,
        organization_id=str(key.organization_id),
        display_name=key.owner_label,
        email=key.email,
        has_password=key.has_password,
        organization_setup_complete=is_complete(profile),
    )


@router.patch("/v1/me", response_model=MeResponse)
async def update_me(
    body: UpdateProfileRequest,
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> MeResponse:
    row = await db.get(ApiKey, key.id)
    assert row is not None  # the row that authenticated this request
    row.owner_label = body.display_name
    await db.commit()
    profile = await db.get(OrganizationProfile, key.organization_id)
    return MeResponse(
        role=row.role,
        organization_id=str(row.organization_id),
        display_name=row.owner_label,
        email=row.email,
        has_password=row.password_hash is not None,
        organization_setup_complete=is_complete(profile),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_me_route.py -v`
Expected: PASS (all tests — existing ones must still pass since `mock_db.get` in this file likely already returns `None`/a `MagicMock` for calls other than the `ApiKey` row; confirm each pre-existing test's `mock_db.get` stub still resolves sensibly for the new `OrganizationProfile` lookup, adjusting any that don't)

- [ ] **Step 6: Run mypy and ruff**

Run: `uv run mypy src/regradar/schemas/me.py src/regradar/api/routers/me.py && uv run ruff check src/regradar/schemas/me.py src/regradar/api/routers/me.py tests/unit/api/test_me_route.py`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add src/regradar/schemas/me.py src/regradar/api/routers/me.py tests/unit/api/test_me_route.py
git commit -m "feat: surface organization_setup_complete on /v1/me"
```

---

### Task 6: Frontend — "Create an organization" signup path on the Login page

**Files:**
- Modify: `frontend/src/pages/Login.tsx`
- Test: `frontend/src/pages/Login.test.tsx` (create if no test file exists for this page yet — check first)

**Interfaces:**
- Consumes: `POST /v1/auth/signup-org` (Task 2) with body `{org_name, display_name, email, password}`.
- Produces: nothing consumed by later tasks — this is a leaf UI change.

- [ ] **Step 1: Check for an existing test file and its conventions**

Run: `find frontend/src/pages -iname "Login.test.*"` and, if found, read it to match its existing render/mock conventions (likely `@testing-library/react` + `vi.mock('../lib/api')`, mirroring how other page tests in this project mock `apiFetch`). If no test file exists for `Login.tsx` today, skip Steps 2–3 and proceed straight to Step 4 (the codebase evidently doesn't unit-test this page yet, and this plan should not introduce a new testing convention unilaterally without following an existing pattern in the file structure).

- [ ] **Step 2 (only if a test file exists): Write the failing test**

Add a test asserting that selecting "Create an organization" mode and submitting posts to `/v1/auth/signup-org` with the four expected fields, following whatever mocking convention the existing file already uses for `apiFetch`.

- [ ] **Step 3 (only if a test file exists): Run it to verify it fails**

Run: `cd frontend && npm test -- Login`
Expected: FAIL — the new mode doesn't exist yet

- [ ] **Step 4: Add the `signup-org` mode**

Edit `frontend/src/pages/Login.tsx`. Change the `Mode` type and add state:

```typescript
type Mode = 'login' | 'signup' | 'signup-org'
```

Add `orgName` state alongside the existing signup fields:

```typescript
  const [orgName, setOrgName] = useState('')
```

Update `handleSubmit` — add a branch for `signup-org` before the existing `signup` branch:

```typescript
  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    setSignupSuccess(false)
    if ((mode === 'signup' || mode === 'signup-org') && password !== confirmPassword) {
      setFormError("Passwords don't match.")
      return
    }
    setSubmitting(true)
    try {
      if (mode === 'login') {
        await apiFetch('/v1/auth/login', {
          method: 'POST',
          body: JSON.stringify({ email, password }),
          skipAuthRedirect: true,
        })
        window.location.href = '/filings'
        return
      }

      if (mode === 'signup-org') {
        await apiFetch('/v1/auth/signup-org', {
          method: 'POST',
          body: JSON.stringify({
            org_name: orgName,
            display_name: displayName,
            email,
            password,
          }),
          skipAuthRedirect: true,
        })
        setMode('login')
        setSignupSuccess(true)
        setPassword('')
        setConfirmPassword('')
        setDisplayName('')
        setOrgName('')
        return
      }

      await apiFetch('/v1/auth/signup', {
        method: 'POST',
        body: JSON.stringify({
          email,
          password,
          display_name: displayName || undefined,
          invite_code: inviteCode,
          role,
        }),
        skipAuthRedirect: true,
      })
      setMode('login')
      setSignupSuccess(true)
      setPassword('')
      setConfirmPassword('')
      setDisplayName('')
      setInviteCode('')
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }
```

Update the heading and form body — replace the `mode === 'signup'` conditional block that renders invite code/role/name with a three-way branch, and add the org-name field for `signup-org`:

```tsx
            <h1 className="mb-1 text-xl font-semibold text-slate-900">
              {mode === 'login' ? 'Welcome back' : mode === 'signup-org' ? 'Create your organization' : 'Create your account'}
            </h1>
```

```tsx
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          {mode === 'signup' && (
            <>
              <Input
                label="Invite code"
                required
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value)}
                placeholder="rrinv_..."
              />
              <div className="flex flex-col gap-1.5">
                <label htmlFor="signup-role" className="text-sm font-medium text-slate-900">
                  Role
                </label>
                <select
                  id="signup-role"
                  value={role}
                  onChange={(e) => setRole(e.target.value as typeof role)}
                  className="h-10 rounded-md border border-slate-300 px-3 text-sm text-slate-900 focus:border-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-600"
                >
                  {SELF_SELECTABLE_ROLES.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
              <Input
                label="Name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Your name"
              />
            </>
          )}
          {mode === 'signup-org' && (
            <>
              <Input
                label="Organization name"
                required
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                placeholder="Acme Corp"
              />
              <Input
                label="Your name"
                required
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Jane Admin"
              />
            </>
          )}
          <Input
            label="Email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
          />
          <div className="relative">
            <Input
              label="Password"
              type={showPassword ? 'text' : 'password'}
              required
              minLength={mode !== 'login' ? 8 : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode !== 'login' ? 'At least 8 characters' : undefined}
              className="pr-14"
            />
            <button
              type="button"
              onClick={() => setShowPassword((show) => !show)}
              className="absolute bottom-0 right-0 flex h-10 items-center pr-3 text-xs font-medium text-primary-600 hover:underline"
            >
              {showPassword ? 'Hide' : 'Show'}
            </button>
          </div>
          {mode !== 'login' && (
            <Input
              label="Confirm password"
              type={showPassword ? 'text' : 'password'}
              required
              minLength={8}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Re-enter your password"
            />
          )}
          {formError && <p className="text-sm text-risk-critical">{formError}</p>}
          <Button type="submit" variant="primary" size="lg" className="w-full" loading={submitting}>
            {mode === 'login' ? 'Sign in' : mode === 'signup-org' ? 'Create organization' : 'Create account'}
          </Button>
        </form>

        <p className="mt-4 text-center text-sm text-slate-500">
          {mode === 'login' ? (
            <>
              Have an invite code?{' '}
              <button
                type="button"
                className="font-medium text-primary-600 hover:underline"
                onClick={() => {
                  setMode('signup')
                  setFormError(null)
                  setSignupSuccess(false)
                }}
              >
                Sign up
              </button>
              {' · '}
              New here?{' '}
              <button
                type="button"
                className="font-medium text-primary-600 hover:underline"
                onClick={() => {
                  setMode('signup-org')
                  setFormError(null)
                  setSignupSuccess(false)
                }}
              >
                Create an organization
              </button>
            </>
          ) : (
            <button
              type="button"
              className="font-medium text-primary-600 hover:underline"
              onClick={() => {
                setMode('login')
                setFormError(null)
                setSignupSuccess(false)
                setConfirmPassword('')
                setInviteCode('')
                setOrgName('')
              }}
            >
              Sign in
            </button>
          )}
        </p>
```

- [ ] **Step 5 (only if a test file exists): Run tests to verify they pass**

Run: `cd frontend && npm test -- Login`
Expected: PASS

- [ ] **Step 6: Run the frontend type checker and linter**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/Login.tsx
git commit -m "feat: add self-serve 'create an organization' signup path"
```

---

### Task 7: Frontend — `/onboarding` page and route guard

**Files:**
- Create: `frontend/src/pages/Onboarding.tsx`
- Modify: `frontend/src/auth/AuthContext.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `MeResponse.organization_setup_complete` (Task 5), `GET/PUT /v1/organizations/me/profile` (Task 3).
- Produces: `AuthState.organizationSetupComplete: boolean`, consumed by `App.tsx`'s `ProtectedRoute`.

- [ ] **Step 1: Add `organizationSetupComplete` to `AuthContext`**

Edit `frontend/src/auth/AuthContext.tsx`:

```typescript
interface MeResponse {
  role: Role
  organization_id: string | null
  display_name: string
  organization_setup_complete: boolean
}

export interface AuthState {
  status: 'loading' | 'authenticated' | 'unauthenticated'
  role: Role | null
  organizationId: string | null
  displayName: string | null
  organizationSetupComplete: boolean
}

const _INITIAL_STATE: AuthState = {
  status: 'loading',
  role: null,
  organizationId: null,
  displayName: null,
  organizationSetupComplete: true,
}
```

(`organizationSetupComplete: true` in the initial/unauthenticated state — `ProtectedRoute`'s `status === 'loading'`/`'unauthenticated'` checks already short-circuit before this field is ever consulted, so its default here never matters in practice; `true` just avoids a misleading "incomplete" flash before the real value loads.)

Update `checkSession`'s success branch:

```typescript
  const checkSession = useCallback(() => {
    apiFetch<MeResponse>('/v1/me')
      .then((me) =>
        setState({
          status: 'authenticated',
          role: me.role,
          organizationId: me.organization_id,
          displayName: me.display_name,
          organizationSetupComplete: me.organization_setup_complete,
        }),
      )
      .catch((err) => {
        if (!(err instanceof ApiError) || err.status !== 401) {
          setState((s) => ({ ...s, status: 'unauthenticated' }))
        }
      })
  }, [])
```

- [ ] **Step 2: Write the Onboarding page**

Create `frontend/src/pages/Onboarding.tsx`:

```tsx
import { useState, type FormEvent } from 'react'

import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { Input } from '../components/Input'
import { useAuth } from '../auth/useAuth'
import { ApiError, apiFetch } from '../lib/api'

function toList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
}

export function Onboarding() {
  const { role } = useAuth()
  const [industry, setIndustry] = useState('')
  const [businessDescription, setBusinessDescription] = useState('')
  const [watchlistEntities, setWatchlistEntities] = useState('')
  const [products, setProducts] = useState('')
  const [riskPriorities, setRiskPriorities] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (role !== 'admin') {
    return (
      <div className="mx-auto max-w-lg p-8">
        <Card>
          <h1 className="mb-2 text-lg font-semibold text-slate-900">Almost there</h1>
          <p className="text-sm text-slate-500">
            Your organization&apos;s Admin needs to finish setting up your organization&apos;s
            profile before filings can be scored. Check back soon.
          </p>
        </Card>
      </div>
    )
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    setSubmitting(true)
    try {
      await apiFetch('/v1/organizations/me/profile', {
        method: 'PUT',
        body: JSON.stringify({
          industry,
          business_description: businessDescription,
          watchlist_entities: toList(watchlistEntities),
          products: toList(products),
          risk_priorities: toList(riskPriorities),
        }),
      })
      // Forces AuthProvider's /v1/me to re-run so organizationSetupComplete
      // flips before ProtectedRoute evaluates again — the same full-reload
      // convention Login.tsx already uses after any auth-state-changing action.
      window.location.href = '/filings'
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
      setSubmitting(false)
    }
  }

  return (
    <div className="mx-auto max-w-lg p-8">
      <Card>
        <h1 className="mb-1 text-xl font-semibold text-slate-900">Tell us about your organization</h1>
        <p className="mb-6 text-sm text-slate-500">
          This is what RegRadar uses to score how much a filing actually matters to your
          business — not just how severe it is.
        </p>
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <Input
            label="Industry"
            required
            value={industry}
            onChange={(e) => setIndustry(e.target.value)}
            placeholder="e.g. Biotechnology"
          />
          <div className="flex flex-col gap-1.5">
            <label htmlFor="business-description" className="text-sm font-medium text-slate-900">
              Business description
            </label>
            <textarea
              id="business-description"
              required
              value={businessDescription}
              onChange={(e) => setBusinessDescription(e.target.value)}
              className="min-h-24 rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-600"
              placeholder="What does your organization do?"
            />
          </div>
          <Input
            label="Watchlist entities (comma-separated)"
            required
            value={watchlistEntities}
            onChange={(e) => setWatchlistEntities(e.target.value)}
            placeholder="Acme Corp, Example Inc"
          />
          <Input
            label="Products (comma-separated)"
            required
            value={products}
            onChange={(e) => setProducts(e.target.value)}
            placeholder="Widget Pro, Widget Lite"
          />
          <Input
            label="Risk priorities (comma-separated)"
            required
            value={riskPriorities}
            onChange={(e) => setRiskPriorities(e.target.value)}
            placeholder="Data privacy, Environmental compliance"
          />
          {formError && <p className="text-sm text-risk-critical">{formError}</p>}
          <Button type="submit" variant="primary" size="lg" className="w-full" loading={submitting}>
            Save and continue
          </Button>
        </form>
      </Card>
    </div>
  )
}
```

- [ ] **Step 3: Wire the route and the guard**

Edit `frontend/src/App.tsx`. Update the import for `useLocation`:

```typescript
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
```

Add the `Onboarding` import:

```typescript
import { Onboarding } from './pages/Onboarding'
```

Update `ProtectedRoute`:

```typescript
function ProtectedRoute({ children, navItem }: { children: ReactNode; navItem?: NavItem }) {
  const { status, role, organizationSetupComplete } = useAuth()
  const location = useLocation()

  if (status === 'loading') {
    return <div className="flex min-h-screen items-center justify-center text-slate-500">Loading…</div>
  }
  if (status === 'unauthenticated') {
    return <Navigate to="/login" replace />
  }
  if (!organizationSetupComplete && location.pathname !== '/onboarding') {
    return <Navigate to="/onboarding" replace />
  }
  if (navItem && !canSeeNavItem(role, navItem)) {
    return <Navigate to="/filings" replace />
  }
  return <AppShell>{children}</AppShell>
}
```

Add the route (alongside the other `<Route>` entries, before the `path="*"` catch-all):

```tsx
        <Route
          path="/onboarding"
          element={
            <ProtectedRoute>
              <Onboarding />
            </ProtectedRoute>
          }
        />
```

- [ ] **Step 4: Run the frontend type checker and linter**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: no errors

- [ ] **Step 5: Manual verification**

Start the dev server (`cd frontend && npm run dev`, with the backend running against a real or local Postgres with migration 0027 applied) and walk through: sign up via "Create an organization" → log in → confirm redirect to `/onboarding` → submit the form → confirm redirect to `/filings` with no further onboarding redirect. Then, as a separate non-Admin account in an org with no profile, confirm `/onboarding` shows the "Almost there" message instead of the form.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/Onboarding.tsx frontend/src/auth/AuthContext.tsx frontend/src/App.tsx
git commit -m "feat: add /onboarding page and route guard on organization setup"
```

---

## Self-Review Notes

- **Spec coverage:** signup-org (Task 2), OrganizationProfile API (Task 3), hard pipeline gate (Task 4), onboarding-required routing (Tasks 5–7), Admin-only edit-after-onboarding (Task 3's `require`-Admin check on `PUT`, reused unchanged by the later `OrganizationSettings.tsx` this spec explicitly says it doesn't build) are all covered.
- **Deviation from the spec's data-model aside:** the spec's Data Model section floated "a shared `require_admin` dependency, extracted from the six existing inline checks" as part of this work. Task 3 does NOT do this — it keeps the existing per-file `if key.role != ApiKeyRole.ADMIN: raise ApiError(...)` convention (matching `config.py`/`invites.py`'s established pattern) instead of refactoring six unrelated files. This is a deliberate scope-narrowing: the six-file refactor is real but unrelated to onboarding, and pulling it in here would touch files this feature has no functional reason to touch.
- **Deviation from the spec's RLS aside:** the spec floated "a service-context write after the require_admin check" as the mechanism for `PUT /v1/organizations/me/profile`. Task 1 does something more correct instead — fixes `organization_profiles`' RLS policy itself (mirroring `source_configs`' established Admin-direct-write pattern from migration 0010), so the route needs no service-role workaround at all. This is a strictly better implementation of the same requirement, not a scope change.
- **Type consistency:** `OrganizationProfileRequest`/`Response` field names (Task 3) match exactly what Task 7's `Onboarding.tsx` sends/expects (`industry`, `business_description`, `watchlist_entities`, `products`, `risk_priorities`). `MeResponse.organization_setup_complete` (Task 5) matches `AuthContext.tsx`'s `MeResponse.organization_setup_complete` (Task 7) and is mapped to `organizationSetupComplete` consistently.
- **No placeholders:** every step has real, complete code — no TODOs.
