# ORG-11 — Organization-Aware Relevance Scoring & Role-Routed Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score every filing's `priority_score` against the *receiving organization's* actual
business (industry, watchlist entities, products, risk priorities), and route alerts to the role
whose domain a filing falls into, with content explaining what happened, why it matters to that
org, and what to do.

**Architecture:** Two new per-organization config tables (`organization_profiles`,
`organization_role_delivery_settings`) feed a new pure/sync `relevance_node` inserted into the
existing LangGraph pipeline between `analyze` and `summarize`. It makes one LLM call (same
tiered-router/retry pattern as `analysis_agent.py`) to score relevance and explain it, which
`pipeline_tasks.py` combines with the existing `risk_level` into `Filing.priority_score`.
`deliver_node` gains a new, additive fan-out step that routes to per-role Slack/email
destinations using a new `roles_for_domain()` helper — the inverse of the role→domain map that
already exists for dashboards in `domain_scope.py`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 (async), Alembic, LangGraph, OpenAI-compatible client
(local Ollama or real OpenAI via `tiered_router.py`), pytest.

## Global Constraints

- Every new pipeline node must be pure/sync with no DB access, matching every node in
  `agents/graph.py` except `retrieve_node`/`deliver_node` — this project's established
  convention (see `analysis_agent.py`'s and `triage_agent.py`'s module docstrings).
- A relevance/scoring failure must never fail or block the pipeline — degrade to a neutral
  default instead, matching `spot_check_classification`'s "never raises" convention in
  `triage_agent.py`.
- Filing visibility/delivery is never suppressed by relevance — every filing an org is
  subscribed to via `source_configs` is still delivered; relevance only affects
  `priority_score` ranking and role-routed alert content.
- New tables follow the `organization_delivery_settings` precedent: service-role-only RLS
  (`FOR ALL USING (current_setting('app.current_role', true) = 'service')`), no admin-facing API
  yet — that's a separate future ticket, matching 0012's own documented precedent.
- New enum-value/JSON-schema fields use the existing `pg_enum_values`/`values_callable` pattern
  everywhere a Postgres ENUM type is involved.

---

### Task 1: `organization_profiles` table + `OrganizationProfile` model

**Files:**
- Create: `migrations/versions/0024_add_organization_profiles.py`
- Create: `src/regradar/models/organization_profile.py`
- Test: `tests/integration/test_organization_profiles_migration.py`

**Interfaces:**
- Produces: `OrganizationProfile` ORM class with columns `organization_id` (PK, UUID),
  `industry: str | None`, `business_description: str | None`, `watchlist_entities: list[str]`,
  `products: list[str]`, `risk_priorities: list[str]`, `updated_at: datetime`.

- [ ] **Step 1: Write the migration**

```python
"""ORG-11 — per-organization business profile for relevance scoring.

Separate table rather than columns on `organizations`, matching
organization_delivery_settings' (0012) precedent: keeps `Organization`
the minimal RLS scaffold its own docstring says it's meant to be. No
admin-facing API to manage this exists yet — matching 0012's own
documented "that's a natural future ticket, not this one."

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-16
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

_IS_SERVICE = "current_setting('app.current_role', true) = 'service'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE organization_profiles (
            organization_id UUID PRIMARY KEY REFERENCES organizations(id),
            industry TEXT,
            business_description TEXT,
            watchlist_entities TEXT[] NOT NULL DEFAULT '{}',
            products TEXT[] NOT NULL DEFAULT '{}',
            risk_priorities TEXT[] NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("ALTER TABLE organization_profiles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organization_profiles FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY organization_profiles_service ON organization_profiles "
        f"FOR ALL USING ({_IS_SERVICE}) WITH CHECK ({_IS_SERVICE})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON organization_profiles TO regradar_app")


def downgrade() -> None:
    op.execute("DROP POLICY organization_profiles_service ON organization_profiles")
    op.execute("DROP TABLE organization_profiles")
```

- [ ] **Step 2: Write the ORM model**

```python
"""ORM model for `organization_profiles` (ORG-11) — per-organization business
context (industry, watchlist entities, products, risk priorities) the
Relevance Agent uses to score how much a filing matters to *this*
organization's business, not just how objectively severe it is.

Separate table rather than columns on `organizations` itself, matching
organization_delivery_settings' precedent: keeps `Organization` the
minimal RLS scaffold its own docstring says it's meant to be.
"""

import uuid
from datetime import datetime

from sqlalchemy import ARRAY, ForeignKey, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from regradar.core.db import Base


class OrganizationProfile(Base):
    __tablename__ = "organization_profiles"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), primary_key=True
    )
    industry: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    watchlist_entities: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    products: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    risk_priorities: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
```

- [ ] **Step 3: Write the migration up/down/up integration test**

```python
"""Live-Postgres verification that migration 0024 is reversible, matching
this project's established migration-testing convention."""

import subprocess

import pytest

pytestmark = pytest.mark.live


def _run_alembic(*args: str) -> None:
    subprocess.run(["alembic", *args], check=True)


def test_0024_upgrade_downgrade_upgrade_cycle() -> None:
    _run_alembic("upgrade", "0024")
    _run_alembic("downgrade", "0023")
    _run_alembic("upgrade", "0024")
```

- [ ] **Step 4: Run the real upgrade/downgrade/upgrade cycle against Postgres**

Run: `alembic upgrade 0024 && alembic downgrade 0023 && alembic upgrade 0024`
Expected: all three commands exit 0, no errors.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0024_add_organization_profiles.py \
        src/regradar/models/organization_profile.py \
        tests/integration/test_organization_profiles_migration.py
git commit -m "feat(org-11): add organization_profiles table and model"
```

---

### Task 2: `organization_role_delivery_settings` table + model

**Files:**
- Create: `migrations/versions/0025_add_organization_role_delivery_settings.py`
- Create: `src/regradar/models/organization_role_delivery_settings.py`
- Test: `tests/integration/test_organization_role_delivery_settings_migration.py`

**Interfaces:**
- Consumes: `ApiKeyRole`, `pg_enum_values` from `regradar.models.enums` (existing).
- Produces: `OrganizationRoleDeliverySettings` ORM class, composite PK
  `(organization_id: uuid.UUID, role: ApiKeyRole)`, columns `slack_webhook_url: str | None`,
  `email: str | None`, `updated_at: datetime`.

- [ ] **Step 1: Write the migration**

```python
"""ORG-11 — per-role alert destinations (Slack channel / email), additive
to the existing org-wide organization_delivery_settings.

Reuses the existing `api_key_role` Postgres enum type (defined by the
api_keys table's migration) rather than creating a new one — role names
must stay a single source of truth. Same service-only RLS precedent as
organization_delivery_settings (0012): no admin-facing API yet.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-16
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

_IS_SERVICE = "current_setting('app.current_role', true) = 'service'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE organization_role_delivery_settings (
            organization_id UUID NOT NULL REFERENCES organizations(id),
            role api_key_role NOT NULL,
            slack_webhook_url TEXT,
            email TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (organization_id, role)
        )
        """
    )
    op.execute("ALTER TABLE organization_role_delivery_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organization_role_delivery_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY organization_role_delivery_settings_service "
        "ON organization_role_delivery_settings "
        f"FOR ALL USING ({_IS_SERVICE}) WITH CHECK ({_IS_SERVICE})"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON organization_role_delivery_settings TO regradar_app"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY organization_role_delivery_settings_service "
        "ON organization_role_delivery_settings"
    )
    op.execute("DROP TABLE organization_role_delivery_settings")
```

- [ ] **Step 2: Write the ORM model**

```python
"""ORM model for `organization_role_delivery_settings` (ORG-11) — per-role
Slack channel / email alert destinations, additive to the org-wide
`organization_delivery_settings` row. A filing's domain picks which
role(s) it routes to via `domain_scope.roles_for_domain`; this table
supplies where that role's alert actually gets sent.
"""

import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from regradar.core.db import Base
from regradar.models.enums import ApiKeyRole, pg_enum_values


class OrganizationRoleDeliverySettings(Base):
    __tablename__ = "organization_role_delivery_settings"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), primary_key=True
    )
    role: Mapped[ApiKeyRole] = mapped_column(
        SAEnum(ApiKeyRole, name="api_key_role", values_callable=pg_enum_values), primary_key=True
    )
    slack_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
```

- [ ] **Step 3: Write the migration up/down/up integration test**

```python
"""Live-Postgres verification that migration 0025 is reversible."""

import subprocess

import pytest

pytestmark = pytest.mark.live


def _run_alembic(*args: str) -> None:
    subprocess.run(["alembic", *args], check=True)


def test_0025_upgrade_downgrade_upgrade_cycle() -> None:
    _run_alembic("upgrade", "0025")
    _run_alembic("downgrade", "0024")
    _run_alembic("upgrade", "0025")
```

- [ ] **Step 4: Run the real upgrade/downgrade/upgrade cycle against Postgres**

Run: `alembic upgrade 0025 && alembic downgrade 0024 && alembic upgrade 0025`
Expected: all three commands exit 0, no errors.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0025_add_organization_role_delivery_settings.py \
        src/regradar/models/organization_role_delivery_settings.py \
        tests/integration/test_organization_role_delivery_settings_migration.py
git commit -m "feat(org-11): add organization_role_delivery_settings table and model"
```

---

### Task 3: `filings` relevance columns

**Files:**
- Create: `migrations/versions/0026_add_filing_relevance_columns.py`
- Modify: `src/regradar/models/filing.py`
- Test: `tests/integration/test_filing_relevance_columns_migration.py`

**Interfaces:**
- Produces: `Filing.relevance_rationale: str | None`, `Filing.recommended_action: str | None`,
  `Filing.matched_signals: dict | None` (JSONB). `Filing.priority_score` already exists
  (unchanged column, just finally gets written by Task 6/8).

- [ ] **Step 1: Write the migration**

```python
"""ORG-11 — org-relevance explanation columns on filings.

Flat columns, matching how domain/risk_level/priority_score/
classification_confidence already live directly on Filing rather than a
side table (small, 1:1 data, always read alongside the rest of the row —
unlike Extraction/Brief, which are large enough to warrant their own
tables).

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-16
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE filings ADD COLUMN relevance_rationale TEXT")
    op.execute("ALTER TABLE filings ADD COLUMN recommended_action TEXT")
    op.execute("ALTER TABLE filings ADD COLUMN matched_signals JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE filings DROP COLUMN matched_signals")
    op.execute("ALTER TABLE filings DROP COLUMN recommended_action")
    op.execute("ALTER TABLE filings DROP COLUMN relevance_rationale")
```

- [ ] **Step 2: Add the columns to the `Filing` ORM model**

In `src/regradar/models/filing.py`, add `JSONB` to the existing
`from sqlalchemy.dialects.postgresql import ...` import line:

```python
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
```

Then add three columns immediately after the existing `priority_score` column:

```python
    priority_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevance_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_signals: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

- [ ] **Step 3: Write the migration up/down/up integration test**

```python
"""Live-Postgres verification that migration 0026 is reversible."""

import subprocess

import pytest

pytestmark = pytest.mark.live


def _run_alembic(*args: str) -> None:
    subprocess.run(["alembic", *args], check=True)


def test_0026_upgrade_downgrade_upgrade_cycle() -> None:
    _run_alembic("upgrade", "0026")
    _run_alembic("downgrade", "0025")
    _run_alembic("upgrade", "0026")
```

- [ ] **Step 4: Run the real upgrade/downgrade/upgrade cycle against Postgres**

Run: `alembic upgrade 0026 && alembic downgrade 0025 && alembic upgrade 0026`
Expected: all three commands exit 0, no errors.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0026_add_filing_relevance_columns.py \
        src/regradar/models/filing.py \
        tests/integration/test_filing_relevance_columns_migration.py
git commit -m "feat(org-11): add relevance_rationale/recommended_action/matched_signals to filings"
```

---

### Task 4: `PipelineState` gains `org_profile` and `relevance`

**Files:**
- Modify: `src/regradar/agents/state.py`
- Test: `tests/unit/agents/test_state.py` (create if it doesn't exist)

**Interfaces:**
- Produces: `OrgProfileSnapshot` (fields: `industry: str | None = None`,
  `business_description: str | None = None`, `watchlist_entities: list[str] = []`,
  `products: list[str] = []`, `risk_priorities: list[str] = []`), `RelevanceResult` (fields:
  `relevance_score: float`, `matched_signals: dict`, `rationale: str`, `recommended_action: str`,
  `model_used: str | None = None`), and `PipelineState.org_profile: OrgProfileSnapshot | None =
  None`, `PipelineState.relevance: RelevanceResult | None = None`.

- [ ] **Step 1: Write a failing test asserting the new fields default to `None`**

```python
import uuid

from regradar.agents.state import OrgProfileSnapshot, PipelineState, RelevanceResult


def test_pipeline_state_defaults_org_profile_and_relevance_to_none() -> None:
    state = PipelineState(filing_id=uuid.uuid4(), raw_text="text")
    assert state.org_profile is None
    assert state.relevance is None


def test_org_profile_snapshot_defaults_to_empty_lists() -> None:
    snapshot = OrgProfileSnapshot()
    assert snapshot.watchlist_entities == []
    assert snapshot.products == []
    assert snapshot.risk_priorities == []


def test_relevance_result_requires_its_four_core_fields() -> None:
    result = RelevanceResult(
        relevance_score=0.8,
        matched_signals={"watchlist_entities": ["Acme Corp"]},
        rationale="Acme Corp is on your watchlist.",
        recommended_action="Review the filing for competitive impact.",
    )
    assert result.relevance_score == 0.8
    assert result.model_used is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/agents/test_state.py -v`
Expected: FAIL with `ImportError: cannot import name 'OrgProfileSnapshot'`

- [ ] **Step 3: Add the two new models and two new `PipelineState` fields**

In `src/regradar/agents/state.py`, add after the existing `ExtractionResult` class:

```python
class OrgProfileSnapshot(BaseModel):
    """The receiving organization's business context (ORG-11's
    OrganizationProfile row, flattened into PipelineState so relevance_node
    stays DB-free like every other pure node)."""

    industry: str | None = None
    business_description: str | None = None
    watchlist_entities: list[str] = []
    products: list[str] = []
    risk_priorities: list[str] = []


class RelevanceResult(BaseModel):
    """Output of the Relevance Agent (ORG-11): how much this filing matters
    to the specific receiving organization, and what to do about it."""

    relevance_score: float
    matched_signals: dict = {}
    rationale: str
    recommended_action: str
    model_used: str | None = None
```

Then add two fields to `PipelineState`, immediately after the existing `briefs` field:

```python
    briefs: BriefSet | None = None
    org_profile: OrgProfileSnapshot | None = None
    relevance: RelevanceResult | None = None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/unit/agents/test_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/regradar/agents/state.py tests/unit/agents/test_state.py
git commit -m "feat(org-11): add OrgProfileSnapshot and RelevanceResult to PipelineState"
```

---

### Task 5: extend `tiered_router.py`'s `Task` literal with `"relevance"`

**Files:**
- Modify: `src/regradar/llm_routing/tiered_router.py:39`
- Test: `tests/unit/llm_routing/test_tiered_router.py` (existing file — extend it)

**Interfaces:**
- Consumes: nothing new.
- Produces: `select_model(risk_level, task="relevance")` now type-checks and behaves identically
  to `"analysis"`/`"summarization"` (routing doesn't differ by task today, per the existing
  `ModelChoice` docstring).

- [ ] **Step 1: Write a failing test**

```python
from regradar.llm_routing.tiered_router import select_model
from regradar.models.enums import RiskLevel


def test_select_model_accepts_relevance_task(monkeypatch) -> None:
    from regradar.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("USE_LOCAL_LLM", "true")
    choice = select_model(RiskLevel.HIGH, task="relevance")
    assert choice.tier == "high"
    get_settings.cache_clear()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/llm_routing/test_tiered_router.py::test_select_model_accepts_relevance_task -v`
Expected: FAIL (mypy/pyright would flag the literal; at runtime it may actually pass since Python
doesn't enforce `Literal` — confirm by running `mypy src/regradar/llm_routing/tiered_router.py`
first, which is the real failure this step must observe: `Argument "task" ... incompatible type
"Literal['relevance']"`)

Run: `mypy src/regradar/llm_routing/tiered_router.py --strict` (or however this repo invokes mypy —
check `pyproject.toml`/`Makefile` for the exact command) and confirm it reports the literal
mismatch before proceeding.

- [ ] **Step 3: Widen the `Task` literal**

In `src/regradar/llm_routing/tiered_router.py`, change:

```python
Task = Literal["analysis", "summarization"]
```

to:

```python
Task = Literal["analysis", "summarization", "relevance"]
```

- [ ] **Step 4: Run the test and the type check to verify they pass**

Run: `pytest tests/unit/llm_routing/test_tiered_router.py::test_select_model_accepts_relevance_task -v`
Expected: PASS

Run the same mypy command from Step 2 again.
Expected: no literal-mismatch error.

- [ ] **Step 5: Commit**

```bash
git add src/regradar/llm_routing/tiered_router.py tests/unit/llm_routing/test_tiered_router.py
git commit -m "feat(org-11): add relevance to tiered_router's Task literal"
```

---

### Task 6: `domain_scope.roles_for_domain`

**Files:**
- Modify: `src/regradar/core/domain_scope.py`
- Test: `tests/unit/core/test_domain_scope.py`

**Interfaces:**
- Consumes: `ROLE_DOMAIN_RESTRICTIONS` (existing module-level dict in the same file).
- Produces: `roles_for_domain(domain: FilingDomain | None) -> list[ApiKeyRole]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/core/test_domain_scope.py`:

```python
from regradar.core.domain_scope import roles_for_domain


def test_roles_for_domain_none_returns_empty_list():
    assert roles_for_domain(None) == []


def test_roles_for_domain_other_returns_empty_list():
    assert roles_for_domain(FilingDomain.OTHER) == []


def test_roles_for_domain_financial_returns_analyst():
    assert roles_for_domain(FilingDomain.FINANCIAL) == [ApiKeyRole.ANALYST]


def test_roles_for_domain_engineering_returns_eng_lead():
    assert roles_for_domain(FilingDomain.ENGINEERING) == [ApiKeyRole.ENG_LEAD]


def test_roles_for_domain_clinical_returns_legal_counsel():
    assert roles_for_domain(FilingDomain.CLINICAL) == [ApiKeyRole.LEGAL_COUNSEL]


def test_roles_for_domain_environmental_returns_legal_counsel():
    assert roles_for_domain(FilingDomain.ENVIRONMENTAL) == [ApiKeyRole.LEGAL_COUNSEL]


def test_roles_for_domain_never_returns_admin_or_executive():
    for domain in FilingDomain:
        assert ApiKeyRole.ADMIN not in roles_for_domain(domain)
        assert ApiKeyRole.EXECUTIVE not in roles_for_domain(domain)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/core/test_domain_scope.py -v`
Expected: FAIL with `ImportError: cannot import name 'roles_for_domain'`

- [ ] **Step 3: Implement `roles_for_domain`**

Append to `src/regradar/core/domain_scope.py`:

```python
def roles_for_domain(domain: FilingDomain | None) -> list[ApiKeyRole]:
    """Inverse of ROLE_DOMAIN_RESTRICTIONS: which domain-restricted role(s)
    an alert for this filing's domain should route to. Returns [] for
    domain=None (unclassified) or FilingDomain.OTHER (no restricted role
    maps to it) — such filings still reach Admin/Executive via the
    existing org-wide delivery channels, just with no role-specific
    fan-out. ADMIN/EXECUTIVE are never returned — they're
    domain-unrestricted by design (see this module's own docstring) and
    already covered by org-wide delivery."""
    if domain is None:
        return []
    return [
        role
        for role, allowed in ROLE_DOMAIN_RESTRICTIONS.items()
        if allowed is not None and domain in allowed
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/core/test_domain_scope.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/regradar/core/domain_scope.py tests/unit/core/test_domain_scope.py
git commit -m "feat(org-11): add roles_for_domain, the inverse of ROLE_DOMAIN_RESTRICTIONS"
```

---

### Task 7: `relevance_agent.py` — the Relevance Agent

**Files:**
- Create: `src/regradar/agents/relevance_agent.py`
- Test: `tests/unit/agents/test_relevance_agent.py`
- Test (live): `tests/unit/agents/test_relevance_agent_live_smoke.py`

**Interfaces:**
- Consumes: `PipelineState` (with `org_profile`, `extraction`, `domain`, `risk_level` from Tasks
  4/existing), `select_model`/`build_client` from `regradar.llm_routing.tiered_router`
  (`task="relevance"` per Task 5), `SEVERITY_ORDER` from `regradar.agents.triage_agent` (existing).
- Produces: `relevance_node(state: PipelineState) -> PipelineState` (sets `state.relevance`, never
  `None`), `compute_priority_score(risk_level: RiskLevel, relevance_score: float) -> float`.

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the Relevance Agent's org-specific impact scoring.

The OpenAI-compatible client is always mocked — no real Ollama or OpenAI
call in these tests. See test_relevance_agent_live_smoke.py for the one
test allowed to hit the real local Ollama server.
"""

import json
import uuid
from unittest.mock import MagicMock, patch

from regradar.agents.relevance_agent import compute_priority_score, relevance_node
from regradar.agents.state import ExtractionResult, OrgProfileSnapshot, PipelineState
from regradar.llm_routing.tiered_router import ModelChoice
from regradar.models.enums import FilingDomain, RiskLevel

VALID_RELEVANCE_JSON = {
    "relevance_score": 0.9,
    "matched_signals": {
        "watchlist_entities": ["Acme Corp"],
        "products": ["insulin pumps"],
        "industry_alignment": "Same medical device industry.",
    },
    "rationale": "Acme Corp, on your watchlist, had an FDA recall affecting insulin pumps.",
    "recommended_action": "Review your own insulin pump supply chain for the same defect.",
}


def _mock_openai_client(content: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    client.chat.completions.create.return_value = response
    return client


def _fake_model_choice(model: str = "llama3.1") -> ModelChoice:
    return ModelChoice(tier="high", model=model, base_url="http://localhost:11434/v1", api_key="ollama-local")


def _make_state_with_extraction(org_profile: OrgProfileSnapshot | None = None) -> PipelineState:
    return PipelineState(
        filing_id=uuid.uuid4(),
        raw_text="Full filing text.",
        domain=FilingDomain.CLINICAL,
        risk_level=RiskLevel.HIGH,
        extraction=ExtractionResult(
            obligations=[],
            deadlines=[],
            risk_flags=["recall"],
            affected_products=["insulin pumps"],
            key_entities=["Acme Corp"],
            competitor_mentions=["Acme Corp"],
        ),
        org_profile=org_profile
        or OrgProfileSnapshot(
            industry="medical device manufacturing",
            watchlist_entities=["Acme Corp"],
            products=["insulin pumps"],
        ),
    )


def test_relevance_node_populates_relevance_on_valid_response() -> None:
    content = json.dumps(VALID_RELEVANCE_JSON)
    with patch(
        "regradar.agents.relevance_agent._get_llm_client",
        return_value=(_mock_openai_client(content), "llama3.1", _fake_model_choice()),
    ):
        result = relevance_node(_make_state_with_extraction())

    assert result.relevance is not None
    assert result.relevance.relevance_score == 0.9
    assert result.relevance.matched_signals["watchlist_entities"] == ["Acme Corp"]
    assert "Acme Corp" in result.relevance.rationale
    assert result.relevance.model_used == "llama3.1"


def test_relevance_node_retries_once_on_malformed_json_then_succeeds() -> None:
    valid_content = json.dumps(VALID_RELEVANCE_JSON)
    client = MagicMock()
    malformed_response = MagicMock()
    malformed_response.choices = [MagicMock(message=MagicMock(content="not valid json"))]
    valid_response = MagicMock()
    valid_response.choices = [MagicMock(message=MagicMock(content=valid_content))]
    client.chat.completions.create.side_effect = [malformed_response, valid_response]

    with patch(
        "regradar.agents.relevance_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        result = relevance_node(_make_state_with_extraction())

    assert result.relevance is not None
    assert result.relevance.relevance_score == 0.9
    assert client.chat.completions.create.call_count == 2


def test_relevance_node_falls_back_to_neutral_default_after_two_failures() -> None:
    client = MagicMock()
    malformed_response = MagicMock()
    malformed_response.choices = [MagicMock(message=MagicMock(content="still not valid json"))]
    client.chat.completions.create.return_value = malformed_response

    with patch(
        "regradar.agents.relevance_agent._get_llm_client",
        return_value=(client, "llama3.1", _fake_model_choice()),
    ):
        result = relevance_node(_make_state_with_extraction())

    assert result.relevance is not None
    assert result.relevance.relevance_score == 0.5
    assert result.relevance.matched_signals == {}


def test_relevance_node_falls_back_to_neutral_default_when_extraction_missing() -> None:
    state = PipelineState(
        filing_id=uuid.uuid4(),
        raw_text="text",
        domain=FilingDomain.FINANCIAL,
        risk_level=RiskLevel.LOW,
        extraction=None,
    )
    result = relevance_node(state)
    assert result.relevance is not None
    assert result.relevance.relevance_score == 0.5


def test_relevance_node_handles_missing_org_profile() -> None:
    content = json.dumps(VALID_RELEVANCE_JSON)
    state = _make_state_with_extraction(org_profile=None)
    with patch(
        "regradar.agents.relevance_agent._get_llm_client",
        return_value=(_mock_openai_client(content), "llama3.1", _fake_model_choice()),
    ):
        result = relevance_node(state)
    assert result.relevance is not None


def test_compute_priority_score_weights_severity_and_relevance_equally() -> None:
    assert compute_priority_score(RiskLevel.CRITICAL, 0.0) == 50.0
    assert compute_priority_score(RiskLevel.LOW, 1.0) == 50.0
    assert compute_priority_score(RiskLevel.CRITICAL, 1.0) == 100.0
    assert compute_priority_score(RiskLevel.LOW, 0.0) == 0.0
    assert compute_priority_score(RiskLevel.HIGH, 0.5) == round(100 * (0.5 * (2 / 3) + 0.5 * 0.5), 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/agents/test_relevance_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.agents.relevance_agent'`

- [ ] **Step 3: Write `relevance_agent.py`**

```python
"""The Relevance Agent (ORG-11) — scores and explains how much a filing
matters to the *specific* receiving organization's business, not just how
objectively severe the filing is.

relevance_node is a plain sync function — no DB access, matching every
node in agents/graph.py except retrieve_node/deliver_node. Unlike
analyze_node (which leaves state.extraction at None on failure, signalling
needs_review), this node NEVER leaves state.relevance at None —
pipeline_tasks.py's priority_score computation and deliver_node's
role-routed alert content both depend on it unconditionally, so a failure
(LLM error, malformed JSON after retry, or no extraction to reason about)
degrades to a neutral, clearly-labeled default instead of blocking the
pipeline — the same "never raises out of the node" convention
triage_agent.py's spot_check_classification established.
"""

import json
import logging

from openai import APIConnectionError, InternalServerError, OpenAI, RateLimitError

from regradar.agents.state import OrgProfileSnapshot, PipelineState, RelevanceResult
from regradar.agents.triage_agent import SEVERITY_ORDER
from regradar.llm_routing.tiered_router import ModelChoice, build_client, select_model
from regradar.models.enums import RiskLevel

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
_SEVERITY_MAX = max(SEVERITY_ORDER.values())

_NEUTRAL_RATIONALE = (
    "Relevance to your organization's specific business could not be determined "
    "automatically for this filing; review it directly."
)
_NEUTRAL_ACTION = "Review this filing manually to assess impact on your organization."

# Bumped whenever RELEVANCE_SYSTEM_PROMPT changes meaningfully — matches
# the PROMPT_VERSION convention in triage_agent.py/analysis_agent.py.
PROMPT_VERSION = "relevance-v1"

RELEVANCE_SYSTEM_PROMPT = (
    "You are a regulatory risk analyst for a specific organization. Given that "
    "organization's business profile and a filing's extracted obligations, risk "
    "flags, affected products, key entities, and competitor mentions, assess how "
    "relevant and impactful this filing is to THIS organization specifically — not "
    "how severe the filing is in general. Respond with strict JSON only, matching "
    "the required schema exactly."
)

RELEVANCE_RETRY_SUFFIX = (
    " The previous response was invalid — every field is required. Respond with "
    "strict, schema-conformant JSON only."
)

RELEVANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance_score": {"type": "number"},
        "matched_signals": {
            "type": "object",
            "properties": {
                "watchlist_entities": {"type": "array", "items": {"type": "string"}},
                "products": {"type": "array", "items": {"type": "string"}},
                "industry_alignment": {"type": "string"},
            },
            "required": ["watchlist_entities", "products", "industry_alignment"],
        },
        "rationale": {"type": "string"},
        "recommended_action": {"type": "string"},
    },
    "required": ["relevance_score", "matched_signals", "rationale", "recommended_action"],
}


class RelevanceError(Exception):
    """Raised internally when scoring fails validation after retry — caught
    by relevance_node, never propagates out of it."""


def _neutral_result() -> RelevanceResult:
    return RelevanceResult(
        relevance_score=0.5,
        matched_signals={},
        rationale=_NEUTRAL_RATIONALE,
        recommended_action=_NEUTRAL_ACTION,
        model_used=None,
    )


def _get_llm_client(risk_level: RiskLevel | None) -> tuple[OpenAI, str, ModelChoice]:
    choice = select_model(risk_level, task="relevance")
    return build_client(choice), choice.model, choice


def _build_relevance_prompt(state: PipelineState) -> str:
    org_profile = state.org_profile or OrgProfileSnapshot()
    extraction = state.extraction
    return (
        f"Organization profile:\n"
        f"- Industry: {org_profile.industry or 'not specified'}\n"
        f"- Business description: {org_profile.business_description or 'not specified'}\n"
        f"- Watchlist entities: {', '.join(org_profile.watchlist_entities) or 'none'}\n"
        f"- Products/services: {', '.join(org_profile.products) or 'none'}\n"
        f"- Risk priorities (in order): {', '.join(org_profile.risk_priorities) or 'none'}\n\n"
        f"Filing domain: {state.domain.value if state.domain else 'unknown'}\n"
        f"Filing risk level: {state.risk_level.value if state.risk_level else 'unknown'}\n"
        f"Risk flags: {', '.join(extraction.risk_flags) if extraction else 'none'}\n"
        f"Affected products: {', '.join(extraction.affected_products) if extraction else 'none'}\n"
        f"Key entities: {', '.join(extraction.key_entities) if extraction else 'none'}\n"
        f"Competitor mentions: {', '.join(extraction.competitor_mentions) if extraction else 'none'}\n"
        f"Obligations: {extraction.obligations if extraction else []}"
    )


def _call_relevance_model(client: OpenAI, model: str, prompt: str, strict_retry: bool) -> dict:
    system_prompt = RELEVANCE_SYSTEM_PROMPT + (RELEVANCE_RETRY_SUFFIX if strict_retry else "")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "relevance", "schema": RELEVANCE_SCHEMA, "strict": True},
        },
        temperature=0,
    )
    content = response.choices[0].message.content or ""
    return json.loads(content)


def _validate_relevance(parsed: dict) -> None:
    try:
        for key in RELEVANCE_SCHEMA["required"]:
            if key not in parsed:
                raise RelevanceError(f"Missing required field: {key}")
        if not isinstance(parsed["relevance_score"], (int, float)):
            raise RelevanceError(f"relevance_score must be numeric, got {parsed['relevance_score']!r}")
        if not isinstance(parsed["matched_signals"], dict):
            raise RelevanceError("matched_signals must be an object")
        if not isinstance(parsed["rationale"], str) or not isinstance(parsed["recommended_action"], str):
            raise RelevanceError("rationale and recommended_action must be strings")
    except RelevanceError:
        raise
    except Exception as exc:
        raise RelevanceError(f"Malformed relevance response: {exc}") from exc


def compute_priority_score(risk_level: RiskLevel, relevance_score: float) -> float:
    """Equal-weighted blend of objective severity and org-specific
    relevance, on a 0-100 scale. An objectively CRITICAL filing with zero
    org relevance and a LOW-risk filing with perfect org relevance land at
    the same midpoint score — both get surfaced, neither dominates."""
    severity_component = SEVERITY_ORDER[risk_level] / _SEVERITY_MAX
    return round(100 * (0.5 * severity_component + 0.5 * relevance_score), 1)


def relevance_node(state: PipelineState) -> PipelineState:
    """Builds a prompt from org_profile + extraction, calls the model,
    validates+retries once. Falls back to a neutral default — never
    None — if state.extraction is missing, or if the LLM call/parse/
    validation fails twice."""
    if state.extraction is None:
        logger.warning(
            "No extraction available for filing %s; using neutral relevance default", state.filing_id
        )
        return state.model_copy(update={"relevance": _neutral_result()})

    prompt = _build_relevance_prompt(state)
    client, model_name, choice = _get_llm_client(state.risk_level)

    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            parsed = _call_relevance_model(client, model_name, prompt, strict_retry=attempt > 0)
            _validate_relevance(parsed)
            relevance = RelevanceResult(
                relevance_score=float(parsed["relevance_score"]),
                matched_signals=parsed["matched_signals"],
                rationale=parsed["rationale"],
                recommended_action=parsed["recommended_action"],
                model_used=model_name,
            )
            return state.model_copy(update={"relevance": relevance})
        except (APIConnectionError, RateLimitError, InternalServerError, json.JSONDecodeError, RelevanceError) as exc:
            last_error = exc
            logger.warning(
                "Relevance scoring attempt %d failed for filing %s: %s",
                attempt + 1,
                state.filing_id,
                exc,
            )

    logger.error(
        "Relevance scoring failed for filing %s after %d attempts: %s; using neutral default",
        state.filing_id,
        MAX_ATTEMPTS,
        last_error,
    )
    return state.model_copy(update={"relevance": _neutral_result()})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/agents/test_relevance_agent.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 5: Write the live smoke test**

```python
"""Live verification (marked @pytest.mark.live) that a real LLM call
through relevance_agent, given an org profile with a deliberately
matching watchlist entity, actually names that match in the rationale —
not just a plausible-sounding generic string."""

import uuid

import pytest

from regradar.agents.relevance_agent import relevance_node
from regradar.agents.state import ExtractionResult, OrgProfileSnapshot, PipelineState
from regradar.models.enums import FilingDomain, RiskLevel

pytestmark = pytest.mark.live


def test_relevance_node_names_the_matched_watchlist_entity_against_real_llm() -> None:
    state = PipelineState(
        filing_id=uuid.uuid4(),
        raw_text="Acme Corp recalled its insulin pump line due to a battery defect.",
        domain=FilingDomain.CLINICAL,
        risk_level=RiskLevel.HIGH,
        extraction=ExtractionResult(
            obligations=[],
            deadlines=[],
            risk_flags=["recall"],
            affected_products=["insulin pumps"],
            key_entities=["Acme Corp"],
            competitor_mentions=["Acme Corp"],
        ),
        org_profile=OrgProfileSnapshot(
            industry="medical device manufacturing",
            watchlist_entities=["Acme Corp"],
            products=["insulin pumps"],
        ),
    )
    result = relevance_node(state)
    assert result.relevance is not None
    assert "acme" in result.relevance.rationale.lower()
```

- [ ] **Step 6: Run the live test against the real local Ollama server**

Run: `pytest tests/unit/agents/test_relevance_agent_live_smoke.py -v -m live`
Expected: PASS (requires local Ollama running per this project's established live-verification
setup — same prerequisite as `test_analysis_agent_live_smoke.py`)

- [ ] **Step 7: Commit**

```bash
git add src/regradar/agents/relevance_agent.py \
        tests/unit/agents/test_relevance_agent.py \
        tests/unit/agents/test_relevance_agent_live_smoke.py
git commit -m "feat(org-11): add the Relevance Agent (org-specific impact scoring)"
```

---

### Task 8: wire `relevance_node` into the pipeline graph

**Files:**
- Modify: `src/regradar/agents/graph.py`
- Test: `tests/unit/agents/test_graph.py` (create if it doesn't exist, else extend)

**Interfaces:**
- Consumes: `relevance_node` from `regradar.agents.relevance_agent` (Task 7).
- Produces: compiled graph edges `analyze -> relevance -> summarize` (was `analyze -> summarize`).

- [ ] **Step 1: Write the failing test**

```python
"""Structural test that relevance sits between analyze and summarize."""

from regradar.agents.graph import build_graph


def test_graph_places_relevance_between_analyze_and_summarize() -> None:
    graph = build_graph()
    node_names = set(graph.get_graph().nodes.keys())
    assert "relevance" in node_names

    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
    assert ("analyze", "relevance") in edges
    assert ("relevance", "summarize") in edges
    assert ("analyze", "summarize") not in edges
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/agents/test_graph.py -v`
Expected: FAIL — `"relevance" in node_names` is False.

- [ ] **Step 3: Add the node and rewire the edges**

In `src/regradar/agents/graph.py`, add the import:

```python
from regradar.agents.relevance_agent import relevance_node
```

Add the node registration alongside the existing ones:

```python
    graph.add_node("relevance", relevance_node)
```

Replace the existing `graph.add_edge("analyze", "summarize")` line with:

```python
    graph.add_edge("analyze", "relevance")
    graph.add_edge("relevance", "summarize")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/unit/agents/test_graph.py -v`
Expected: PASS

- [ ] **Step 5: Run the full existing agent test suite to confirm nothing else broke**

Run: `pytest tests/unit/agents/ -v`
Expected: PASS — the `route_after_triage`/LOW-risk-skips-retrieve path is untouched; only the
segment after `analyze` changed.

- [ ] **Step 6: Commit**

```bash
git add src/regradar/agents/graph.py tests/unit/agents/test_graph.py
git commit -m "feat(org-11): wire relevance_node into the pipeline between analyze and summarize"
```

---

### Task 9: `pipeline_tasks.py` — load org profile, persist relevance + priority_score

**Files:**
- Modify: `src/regradar/workers/pipeline_tasks.py`
- Modify: `tests/unit/workers/test_pipeline_tasks.py`

**Interfaces:**
- Consumes: `OrganizationProfile` (Task 1), `OrgProfileSnapshot`/`RelevanceResult` (Task 4),
  `compute_priority_score` (Task 7).
- Produces: `_run_pipeline_for_filing` now loads the org profile before building `PipelineState`,
  and (when `result["domain"]` is not `None`) sets `filing.priority_score`,
  `filing.relevance_rationale`, `filing.recommended_action`, `filing.matched_signals` from
  `result.get("relevance")`.

This task's Step 1 is a **mechanical, repo-wide fix** to existing tests before adding new
behavior: every existing test in `test_pipeline_tasks.py` mocks `mock_db.get = AsyncMock
(return_value=filing)`, which returns `filing` for *any* `db.get(...)` call regardless of which
model is requested. Adding a second `db.get(OrganizationProfile, ...)` call means that mock would
incorrectly return the `filing` MagicMock for the profile lookup too, breaking `OrgProfileSnapshot`
construction. Fix this globally first, then add the new behavior.

- [ ] **Step 1: Make every existing `mock_db.get` mock model-aware**

In `tests/unit/workers/test_pipeline_tasks.py`, replace **every** occurrence of the exact line:

```python
    mock_db.get = AsyncMock(return_value=filing)
```

with:

```python
    mock_db.get = AsyncMock(side_effect=lambda model, *args, **kwargs: filing if model is Filing else None)
```

Use a single find-and-replace across the whole file (this exact string appears at every site that
needs it; `Filing` is already imported at the top of this file). Do **not** touch the two
`AsyncMock(return_value=None)` occurrences (those are for the "filing not found" tests) or any
`_mark_filing_failed`-only test — the replacement is safe there too since those tests only ever
call `db.get(Filing, ...)`.

Then find this single assertion:

```python
    mock_db.get.assert_awaited_once_with(Filing, filing_id)
```

and change it to:

```python
    mock_db.get.assert_any_await(Filing, filing_id)
```

(`_run_pipeline_for_filing` will now call `db.get` twice — once for `Filing`, once for
`OrganizationProfile` — so asserting "awaited once" would fail; `assert_any_await` correctly
checks the `Filing` call happened without constraining how many other calls were also made.)

- [ ] **Step 2: Run the full existing test file to confirm the mechanical fix is a no-op behaviorally**

Run: `pytest tests/unit/workers/test_pipeline_tasks.py -v`
Expected: PASS — every test still passes; this step only changed *how* the mocks are wired, not
what they return for the calls that already existed.

- [ ] **Step 3: Write the new failing tests for org-profile loading and relevance persistence**

Append to `tests/unit/workers/test_pipeline_tasks.py`:

```python
from regradar.agents.state import RelevanceResult
from regradar.models.organization_profile import OrganizationProfile


def test_process_filing_loads_org_profile_before_building_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    org_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.organization_id = org_id
    filing.raw_pdf_s3_key = None

    profile_row = MagicMock(spec=OrganizationProfile)
    profile_row.industry = "medical device manufacturing"
    profile_row.business_description = "We make insulin pumps."
    profile_row.watchlist_entities = ["Acme Corp"]
    profile_row.products = ["insulin pumps"]
    profile_row.risk_priorities = ["clinical trial safety"]

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(
        side_effect=lambda model, *args, **kwargs: (
            filing if model is Filing else profile_row if model is OrganizationProfile else None
        )
    )
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory)

    captured_state = {}

    async def _fake_ainvoke(state, config=None):
        captured_state["org_profile"] = state.org_profile
        return {
            "domain": FilingDomain.CLINICAL,
            "risk_level": RiskLevel.HIGH,
            "classification_confidence": 0.9,
            "extraction": None,
            "briefs": None,
            "relevance": None,
            "delivery_status": None,
            "delivery_success": None,
        }

    monkeypatch.setattr(
        pipeline_tasks_module, "build_graph", lambda: MagicMock(ainvoke=_fake_ainvoke)
    )

    process_filing.run(str(filing_id))

    assert captured_state["org_profile"] is not None
    assert captured_state["org_profile"].industry == "medical device manufacturing"
    assert captured_state["org_profile"].watchlist_entities == ["Acme Corp"]


def test_process_filing_persists_priority_score_and_relevance_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.organization_id = uuid.uuid4()
    filing.raw_pdf_s3_key = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(side_effect=lambda model, *args, **kwargs: filing if model is Filing else None)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory)
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.CLINICAL,
                    "risk_level": RiskLevel.HIGH,
                    "classification_confidence": 0.9,
                    "extraction": None,
                    "briefs": None,
                    "relevance": RelevanceResult(
                        relevance_score=0.9,
                        matched_signals={"watchlist_entities": ["Acme Corp"]},
                        rationale="Acme Corp is on your watchlist.",
                        recommended_action="Review the filing.",
                        model_used="llama3.1",
                    ),
                    "delivery_status": None,
                    "delivery_success": None,
                }
            )
        ),
    )

    process_filing.run(str(filing_id))

    assert filing.priority_score == round(100 * (0.5 * (2 / 3) + 0.5 * 0.9), 1)
    assert filing.relevance_rationale == "Acme Corp is on your watchlist."
    assert filing.recommended_action == "Review the filing."
    assert filing.matched_signals == {"watchlist_entities": ["Acme Corp"]}


def test_process_filing_leaves_relevance_fields_unset_when_unclassified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.organization_id = uuid.uuid4()
    filing.raw_pdf_s3_key = None
    filing.priority_score = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(side_effect=lambda model, *args, **kwargs: filing if model is Filing else None)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory)
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": None,
                    "risk_level": None,
                    "classification_confidence": None,
                    "extraction": None,
                    "briefs": None,
                    "relevance": None,
                    "delivery_status": None,
                    "delivery_success": None,
                }
            )
        ),
    )

    process_filing.run(str(filing_id))

    assert filing.priority_score is None
    assert filing.status == FilingStatus.NEEDS_CLASSIFICATION
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `pytest tests/unit/workers/test_pipeline_tasks.py -k "org_profile or priority_score or unclassified" -v`
Expected: FAIL — `org_profile` loading and `priority_score` persistence don't exist yet.

- [ ] **Step 5: Implement the org-profile load and relevance persistence**

In `src/regradar/workers/pipeline_tasks.py`, add imports:

```python
from regradar.agents.relevance_agent import compute_priority_score
from regradar.agents.state import OrgProfileSnapshot
from regradar.models.organization_profile import OrganizationProfile
```

In `_run_pipeline_for_filing`, right after the existing PDF-extraction block and before building
`state`, add the org-profile load:

```python
        profile_row = await db.get(OrganizationProfile, filing.organization_id)
        org_profile = (
            OrgProfileSnapshot(
                industry=profile_row.industry,
                business_description=profile_row.business_description,
                watchlist_entities=list(profile_row.watchlist_entities),
                products=list(profile_row.products),
                risk_priorities=list(profile_row.risk_priorities),
            )
            if profile_row is not None
            else None
        )

        state = PipelineState(
            filing_id=filing.id, raw_text=raw_text, chunks=chunks or None, org_profile=org_profile
        )
```

(this replaces the existing `state = PipelineState(filing_id=filing.id, raw_text=raw_text,
chunks=chunks or None)` line one-for-one.)

In the `else` branch that runs when `result["domain"] is not None` (right after the existing
`filing.classification_confidence = result["classification_confidence"]` line), add:

```python
            relevance_result = result.get("relevance")
            if relevance_result is not None:
                filing.priority_score = compute_priority_score(
                    result["risk_level"], relevance_result.relevance_score
                )
                filing.relevance_rationale = relevance_result.rationale
                filing.recommended_action = relevance_result.recommended_action
                filing.matched_signals = relevance_result.matched_signals
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `pytest tests/unit/workers/test_pipeline_tasks.py -k "org_profile or priority_score or unclassified" -v`
Expected: PASS

- [ ] **Step 7: Run the entire file to confirm no regressions**

Run: `pytest tests/unit/workers/test_pipeline_tasks.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 8: Commit**

```bash
git add src/regradar/workers/pipeline_tasks.py tests/unit/workers/test_pipeline_tasks.py
git commit -m "feat(org-11): load org profile pre-pipeline, persist priority_score and relevance fields"
```

---

### Task 10: `deliver_node` — role-routed alert fan-out

**Files:**
- Modify: `src/regradar/agents/delivery_agent.py`
- Modify: `tests/unit/agents/test_delivery_agent.py`

**Interfaces:**
- Consumes: `roles_for_domain` (Task 6), `OrganizationRoleDeliverySettings` (Task 2),
  `state.relevance: RelevanceResult | None` (Task 4), existing `send_slack_alert`/
  `send_email_alert` (unchanged signatures).
- Produces: `deliver_node` additionally fans out to per-role Slack/email destinations, additive to
  the existing org-wide Slack/email/webhook fan-out.

- [ ] **Step 1: Update the existing idempotency-set construction to also key by recipient**

In `src/regradar/agents/delivery_agent.py`'s `deliver_node`, find:

```python
    existing = await db.execute(
        select(Delivery).where(
            Delivery.filing_id == state.filing_id, Delivery.status == DeliveryStatus.SENT
        )
    )
    already_sent = {(d.channel, d.webhook_id) for d in existing.scalars().all()}
```

and replace it with:

```python
    existing = await db.execute(
        select(Delivery).where(
            Delivery.filing_id == state.filing_id, Delivery.status == DeliveryStatus.SENT
        )
    )
    existing_deliveries = existing.scalars().all()
    already_sent = {(d.channel, d.webhook_id) for d in existing_deliveries}
    already_sent_recipients = {(d.channel, d.recipient) for d in existing_deliveries}
```

(This is a pure refactor — `already_sent` behaves identically; `already_sent_recipients` is new,
used only by the role fan-out added in Step 3.)

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/agents/test_delivery_agent.py`:

```python
from regradar.agents.state import RelevanceResult
from regradar.models.organization_role_delivery_settings import OrganizationRoleDeliverySettings


def _make_role_settings(
    *, slack_webhook_url: str | None = None, email: str | None = None
) -> MagicMock:
    settings_row = MagicMock(spec=OrganizationRoleDeliverySettings)
    settings_row.slack_webhook_url = slack_webhook_url
    settings_row.email = email
    return settings_row


def _make_db_with_role_settings(
    filing: MagicMock,
    existing_deliveries: list,
    webhooks: list,
    *,
    delivery_settings: MagicMock | None = None,
    role_settings: MagicMock | None = None,
) -> AsyncMock:
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[filing, delivery_settings, role_settings])

    deliveries_result = MagicMock()
    deliveries_result.scalars.return_value.all.return_value = existing_deliveries
    webhooks_result = MagicMock()
    webhooks_result.scalars.return_value.all.return_value = webhooks
    query_results = iter([deliveries_result, webhooks_result])

    async def _execute(stmt, *args, **kwargs):
        if "set_config" in getattr(stmt, "text", ""):
            return MagicMock()
        return next(query_results)

    db.execute = AsyncMock(side_effect=_execute)
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


async def test_deliver_node_routes_engineering_filing_to_eng_lead_slack() -> None:
    filing_id = uuid.uuid4()
    filing = _make_filing(filing_id)
    state = _make_state(risk_level=RiskLevel.HIGH)
    state = state.model_copy(
        update={
            "domain": FilingDomain.ENGINEERING,
            "relevance": RelevanceResult(
                relevance_score=0.9,
                matched_signals={"products": ["widget"]},
                rationale="This affects your widget product line.",
                recommended_action="Audit your widget suppliers.",
            ),
        }
    )
    role_settings = _make_role_settings(slack_webhook_url=_SLACK_URL)
    db = _make_db_with_role_settings(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=None,
        role_settings=role_settings,
    )

    with patch(
        "regradar.agents.delivery_agent.send_slack_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=200)),
    ) as mock_send_slack:
        await deliver_node(state, config={"configurable": {"db": db}})

    mock_send_slack.assert_awaited_once()
    call_kwargs = mock_send_slack.call_args.kwargs
    assert call_kwargs["webhook_url"] == _SLACK_URL
    assert "widget" in call_kwargs["cco_summary"]
    assert "Audit your widget suppliers" in call_kwargs["cco_summary"]


async def test_deliver_node_skips_role_fanout_when_no_role_settings_configured() -> None:
    filing_id = uuid.uuid4()
    filing = _make_filing(filing_id)
    state = _make_state(risk_level=RiskLevel.HIGH)
    state = state.model_copy(update={"domain": FilingDomain.ENGINEERING, "relevance": None})
    db = _make_db_with_role_settings(
        filing, existing_deliveries=[], webhooks=[], delivery_settings=None, role_settings=None
    )

    with patch(
        "regradar.agents.delivery_agent.send_slack_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=200)),
    ) as mock_send_slack:
        await deliver_node(state, config={"configurable": {"db": db}})

    mock_send_slack.assert_not_awaited()


async def test_deliver_node_role_fanout_is_additive_to_org_wide_slack() -> None:
    filing_id = uuid.uuid4()
    filing = _make_filing(filing_id)
    state = _make_state(risk_level=RiskLevel.HIGH)
    state = state.model_copy(
        update={
            "domain": FilingDomain.ENGINEERING,
            "relevance": RelevanceResult(
                relevance_score=0.9,
                matched_signals={},
                rationale="Matches your product line.",
                recommended_action="Investigate.",
            ),
        }
    )
    org_wide_settings = _make_delivery_settings(_SLACK_URL)
    role_settings = _make_role_settings(slack_webhook_url="https://hooks.slack.com/services/ROLE")
    db = _make_db_with_role_settings(
        filing,
        existing_deliveries=[],
        webhooks=[],
        delivery_settings=org_wide_settings,
        role_settings=role_settings,
    )

    with patch(
        "regradar.agents.delivery_agent.send_slack_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=200)),
    ) as mock_send_slack:
        await deliver_node(state, config={"configurable": {"db": db}})

    assert mock_send_slack.await_count == 2
    sent_urls = {call.kwargs["webhook_url"] for call in mock_send_slack.await_args_list}
    assert sent_urls == {_SLACK_URL, "https://hooks.slack.com/services/ROLE"}


async def test_deliver_node_role_fanout_email_uses_role_email_recipient() -> None:
    filing_id = uuid.uuid4()
    filing = _make_filing(filing_id)
    state = _make_state(risk_level=RiskLevel.HIGH)
    state = state.model_copy(
        update={
            "domain": FilingDomain.FINANCIAL,
            "relevance": RelevanceResult(
                relevance_score=0.7,
                matched_signals={},
                rationale="Relevant to your reporting obligations.",
                recommended_action="Review your Q3 filing.",
            ),
        }
    )
    role_settings = _make_role_settings(email="analyst-team@example.com")
    db = _make_db_with_role_settings(
        filing, existing_deliveries=[], webhooks=[], delivery_settings=None, role_settings=role_settings
    )

    with patch(
        "regradar.agents.delivery_agent.send_email_alert",
        new=AsyncMock(return_value=DeliveryResult(status=DeliveryStatus.SENT, response_code=202)),
    ) as mock_send_email:
        await deliver_node(state, config={"configurable": {"db": db}})

    mock_send_email.assert_awaited_once()
    assert mock_send_email.call_args.kwargs["recipient"] == "analyst-team@example.com"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/unit/agents/test_delivery_agent.py -k "role" -v`
Expected: FAIL — role fan-out doesn't exist yet, and `db.get` side_effect lists in these new
tests expect a third call that never happens.

- [ ] **Step 4: Implement the role fan-out**

In `src/regradar/agents/delivery_agent.py`, add imports:

```python
from regradar.core.domain_scope import roles_for_domain
from regradar.models.organization_role_delivery_settings import OrganizationRoleDeliverySettings
```

Add a message-building helper near the top of the module (after `_webhook_matches`):

```python
def _build_role_alert_message(
    *, priority_score: float | None, rationale: str, recommended_action: str
) -> str:
    score_text = f"{priority_score:.0f}/100" if priority_score is not None else "not scored"
    return f"Why this matters to you: {rationale} Recommended action: {recommended_action} Priority: {score_text}."
```

Also add, alongside the other module-level imports at the top of the file (not inline in the
function):

```python
from regradar.agents.relevance_agent import compute_priority_score
```

Add the fan-out loop in `deliver_node`, immediately after the existing webhook fan-out `for
webhook in webhooks_result.scalars().all():` loop ends (i.e., as the last block before the
function's final `return`):

```python
    # --- Role-routed alerts (ORG-11) ---
    # Additive to the org-wide Slack/email above — a role channel is a
    # second destination for the same filing, not a replacement.
    #
    # priority_score is recomputed here (via the same compute_priority_score
    # relevance_agent.py uses) rather than read from Filing.priority_score:
    # deliver_node runs inside the same graph invocation that produces
    # state.relevance, before pipeline_tasks.py ever persists
    # Filing.priority_score — there is no persisted value yet to read.
    priority_score = (
        None
        if state.relevance is None or state.risk_level is None
        else compute_priority_score(state.risk_level, state.relevance.relevance_score)
    )
    for role in roles_for_domain(state.domain):
        role_settings = await db.get(OrganizationRoleDeliverySettings, (filing.organization_id, role))
        if role_settings is None or state.relevance is None:
            continue
        role_message = _build_role_alert_message(
            priority_score=priority_score,
            rationale=state.relevance.rationale,
            recommended_action=state.relevance.recommended_action,
        )
        if role_settings.slack_webhook_url and (
            DeliveryChannel.SLACK,
            f"slack:role:{role.value}",
        ) not in already_sent_recipients:
            try:
                result = await send_slack_alert(
                    webhook_url=role_settings.slack_webhook_url,
                    entity_name=filing.entity_name,
                    filing_type=filing.filing_type,
                    filing_url=filing.filing_url,
                    risk_level=state.risk_level,
                    cco_summary=role_message,
                )
            except Exception as exc:  # noqa: BLE001 — one role's channel crashing must not block others
                logger.warning(
                    "Role Slack delivery raised for filing %s role %s: %s",
                    state.filing_id,
                    role.value,
                    exc,
                )
                result = DeliveryResult(
                    status=DeliveryStatus.FAILED,
                    response_code=None,
                    error_message=f"{type(exc).__name__}: {exc}",
                )
            await _record_delivery(
                db,
                filing.id,
                filing.organization_id,
                DeliveryChannel.SLACK,
                f"slack:role:{role.value}",
                result,
            )
            if result.status == DeliveryStatus.SENT:
                any_sent = True
            statuses.append(f"slack_role_{role.value}={result.status.value}")
        if role_settings.email and (DeliveryChannel.EMAIL, role_settings.email) not in already_sent_recipients:
            try:
                result = await send_email_alert(
                    recipient=role_settings.email,
                    entity_name=filing.entity_name,
                    filing_type=filing.filing_type,
                    risk_level=state.risk_level,
                    executive_brief=role_message,
                )
            except Exception as exc:  # noqa: BLE001 — see Slack's comment above
                logger.warning(
                    "Role email delivery raised for filing %s role %s: %s",
                    state.filing_id,
                    role.value,
                    exc,
                )
                result = DeliveryResult(
                    status=DeliveryStatus.FAILED,
                    response_code=None,
                    error_message=f"{type(exc).__name__}: {exc}",
                )
            await _record_delivery(
                db, filing.id, filing.organization_id, DeliveryChannel.EMAIL, role_settings.email, result
            )
            if result.status == DeliveryStatus.SENT:
                any_sent = True
            statuses.append(f"email_role_{role.value}={result.status.value}")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/unit/agents/test_delivery_agent.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 6: Run the full unit test suite to confirm no regressions**

Run: `pytest tests/unit/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/regradar/agents/delivery_agent.py tests/unit/agents/test_delivery_agent.py
git commit -m "feat(org-11): route alerts to per-role Slack/email destinations by filing domain"
```

---

### Task 11: full-suite regression pass and live verification

**Files:** none new — verification only.

- [ ] **Step 1: Run the entire unit test suite**

Run: `pytest tests/unit/ -v`
Expected: PASS, zero failures.

- [ ] **Step 2: Run every new/changed migration's up/down/up cycle together, in sequence, against real Postgres**

Run: `alembic upgrade head && alembic downgrade 0023 && alembic upgrade head`
Expected: all three commands exit 0.

- [ ] **Step 3: Run the live-marked tests**

Run: `pytest tests/unit/agents/test_relevance_agent_live_smoke.py -v -m live`
Expected: PASS (requires local Ollama running, per this project's established live-verification
policy — same as `test_analysis_agent_live_smoke.py`).

- [ ] **Step 4: Manually verify end-to-end via `process-pending`**

Insert a test `Organization`, `OrganizationProfile` (with a distinctive `watchlist_entities`
value), and `OrganizationRoleDeliverySettings` row (Slack webhook pointing at a real or
test-catcher Slack Incoming Webhook URL) for `ApiKeyRole.ENG_LEAD`, ingest one real Engineering-
domain filing, then run this project's existing `process-pending` CLI command. Confirm: (a) the
filing's `priority_score`, `relevance_rationale`, `recommended_action`, and `matched_signals`
columns are populated in the database, and (b) the Eng Lead's configured Slack channel actually
receives a message naming the matched watchlist entity.

- [ ] **Step 5: Commit the plan's completion marker (if this repo tracks that) or simply confirm no uncommitted changes remain**

Run: `git status`
Expected: clean working tree (everything already committed task-by-task above).
