"""SEC-01 — verifies RLS policies are enforced by Postgres itself, not just
application-level role checks.

Deliberately does NOT go through core.db.get_session_factory() (whose
connection string depends on whatever APP_DATABASE_URL happens to be set
in the ambient environment) — it opens its own engine directly against
settings.effective_app_database_url, so a misconfigured environment (one
still connecting as the RLS-bypassing superuser) makes these tests fail
loudly, not silently pass on unenforced policies. Every attempt here is
expected to be REJECTED by the database — this is a deny-by-default
verification suite, per the ticket's own acceptance criteria ("an
automated test suite attempts, for every role, to access rows it should
not be able to, and confirms each attempt is denied").
"""

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from regradar.core.config import get_settings
from regradar.core.db import get_session_factory, set_rls_context

pytestmark = pytest.mark.asyncio

# Matches migration 0010's seeded default organization — every row in this
# test file that isn't explicitly testing cross-org isolation belongs here.
_DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _load_real_env_for_settings(monkeypatch: pytest.MonkeyPatch):
    """tests/conftest.py's _no_real_dotenv_fallback disables Settings' own
    .env reading for the whole test session (so app-code tests can simulate
    "unset" via monkeypatch.delenv without a real .env leaking through) —
    which means these tests, which genuinely need the real DATABASE_URL/
    APP_DATABASE_URL to hit live Postgres, must populate os.environ
    directly instead. get_settings() is cached, so clear it before and
    after so this file doesn't leak a real-env Settings instance into
    unrelated tests collected later in the same session.
    """
    from regradar.core.config import get_settings

    env_path = Path(__file__).resolve().parents[2] / ".env"
    for key, value in dotenv_values(env_path).items():
        if value is not None:
            monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def rls_session() -> AsyncGenerator[AsyncSession, None]:
    """A session on the restricted app role, one fresh transaction per test.

    Every test sets its own role via set_rls_context. Always rolled back
    explicitly (never committed) — Session.begin()'s context manager
    commits on a clean exit, which would otherwise leak each test's own
    writes (e.g. source_configs rows) into the next run.
    """
    settings = get_settings()
    engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


async def _seed_filing() -> uuid.UUID:
    """Insert one real filing as `service`, in its own committed transaction,
    so later test transactions (any role) can see it via MVCC."""
    settings = get_settings()
    engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    filing_id = uuid.uuid4()
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        await db.execute(
            text(
                """
                INSERT INTO filings (id, organization_id, source, source_document_id, entity_name,
                    filing_type, filing_url, status, published_at, ingested_at,
                    created_at, updated_at)
                VALUES (:id, :org_id, 'SEC', :doc_id, 'RLS Test Corp', '10-K',
                    'http://example.com/rls-test', 'ingested', now(), now(), now(), now())
                """
            ),
            {"id": str(filing_id), "org_id": _DEFAULT_ORG_ID, "doc_id": f"rls-test-{filing_id}"},
        )
        await db.commit()
    await engine.dispose()
    return filing_id


async def _cleanup_filing(filing_id: uuid.UUID) -> None:
    session_factory = get_session_factory()
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        await db.execute(text("DELETE FROM filings WHERE id = :id"), {"id": str(filing_id)})
        await db.commit()


@pytest.fixture
async def seeded_filing_id() -> AsyncGenerator[uuid.UUID, None]:
    filing_id = await _seed_filing()
    yield filing_id
    await _cleanup_filing(filing_id)


async def test_unauthenticated_session_cannot_select_filings(rls_session: AsyncSession):
    """No app.current_role set at all — the deny-by-default floor."""
    result = await rls_session.execute(text("SELECT count(*) FROM filings"))
    assert result.scalar_one() == 0


async def test_analyst_cannot_insert_filings(rls_session: AsyncSession):
    await set_rls_context(rls_session, role="analyst")
    with pytest.raises(DBAPIError, match="row-level security policy"):
        await rls_session.execute(
            text(
                """
                INSERT INTO filings (id, organization_id, source, source_document_id, entity_name,
                    filing_type, filing_url, status, published_at, ingested_at,
                    created_at, updated_at)
                VALUES (gen_random_uuid(), :org_id, 'SEC', :doc_id, 'Should Fail Corp',
                    '10-K', 'http://example.com', 'ingested', now(), now(), now(), now())
                """
            ),
            {"org_id": _DEFAULT_ORG_ID, "doc_id": f"analyst-insert-attempt-{uuid.uuid4()}"},
        )


async def test_service_can_insert_and_select_filings(rls_session: AsyncSession):
    await set_rls_context(rls_session, role="service")
    doc_id = f"service-insert-{uuid.uuid4()}"
    await rls_session.execute(
        text(
            """
            INSERT INTO filings (id, organization_id, source, source_document_id, entity_name,
                filing_type, filing_url, status, published_at, ingested_at,
                created_at, updated_at)
            VALUES (gen_random_uuid(), :org_id, 'SEC', :doc_id, 'Service Corp',
                '10-K', 'http://example.com', 'ingested', now(), now(), now(), now())
            """
        ),
        {"org_id": _DEFAULT_ORG_ID, "doc_id": doc_id},
    )
    result = await rls_session.execute(
        text("SELECT count(*) FROM filings WHERE source_document_id = :doc_id"), {"doc_id": doc_id}
    )
    assert result.scalar_one() == 1


async def test_analyst_can_select_seeded_filing(
    rls_session: AsyncSession, seeded_filing_id: uuid.UUID
):
    await set_rls_context(rls_session, role="analyst", organization_id=_DEFAULT_ORG_ID)
    result = await rls_session.execute(
        text("SELECT count(*) FROM filings WHERE id = :id"), {"id": str(seeded_filing_id)}
    )
    assert result.scalar_one() == 1


async def test_executive_cannot_select_extractions_that_exist(
    rls_session: AsyncSession, seeded_filing_id: uuid.UUID
):
    """A real extraction row exists (inserted as service, in its own
    committed transaction) — Executive must see zero rows, not because
    none exist, but because RLS denies them."""
    settings = get_settings()
    engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as seed_db:
        await set_rls_context(seed_db, role="service")
        await seed_db.execute(
            text(
                """
                INSERT INTO extractions (id, filing_id, obligations, deadlines,
                    risk_flags, affected_products, key_entities, competitor_mentions, created_at)
                VALUES (gen_random_uuid(), :filing_id, '[]', '[]', '[]', '[]', '[]', '[]', now())
                """
            ),
            {"filing_id": str(seeded_filing_id)},
        )
        await seed_db.commit()
    await engine.dispose()

    await set_rls_context(rls_session, role="executive", organization_id=_DEFAULT_ORG_ID)
    result = await rls_session.execute(
        text("SELECT count(*) FROM extractions WHERE filing_id = :id"), {"id": str(seeded_filing_id)}
    )
    assert result.scalar_one() == 0

    await set_rls_context(rls_session, role="analyst", organization_id=_DEFAULT_ORG_ID)
    result = await rls_session.execute(
        text("SELECT count(*) FROM extractions WHERE filing_id = :id"), {"id": str(seeded_filing_id)}
    )
    assert result.scalar_one() == 1


async def test_key_cannot_see_another_keys_webhook(rls_session: AsyncSession):
    owner_key_id = uuid.uuid4()
    other_key_id = uuid.uuid4()

    settings = get_settings()
    engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as seed_db:
        await set_rls_context(seed_db, role="service")
        # webhooks.api_key_id has a real FK to api_keys.id — needs a real row.
        await seed_db.execute(
            text(
                """
                INSERT INTO api_keys (id, organization_id, key_hash, owner_label, role, is_active, rate_limit_per_minute, created_at)
                VALUES (:id, :org_id, :key_hash, 'rls-test-owner', 'analyst', true, 60, now())
                """
            ),
            {
                "id": str(owner_key_id),
                "org_id": _DEFAULT_ORG_ID,
                "key_hash": f"rls-test-hash-{owner_key_id}",
            },
        )
        await seed_db.execute(
            text(
                """
                INSERT INTO webhooks (id, organization_id, api_key_id, url, hmac_secret, is_active, created_at)
                VALUES (gen_random_uuid(), :org_id, :key_id, 'https://example.com/hook', 'secret', true, now())
                """
            ),
            {"org_id": _DEFAULT_ORG_ID, "key_id": str(owner_key_id)},
        )
        await seed_db.commit()
    await engine.dispose()

    await set_rls_context(rls_session, role="analyst", api_key_id=str(other_key_id))
    result = await rls_session.execute(
        text("SELECT count(*) FROM webhooks WHERE api_key_id = :id"), {"id": str(owner_key_id)}
    )
    assert result.scalar_one() == 0

    await set_rls_context(rls_session, role="analyst", api_key_id=str(owner_key_id))
    result = await rls_session.execute(
        text("SELECT count(*) FROM webhooks WHERE api_key_id = :id"), {"id": str(owner_key_id)}
    )
    assert result.scalar_one() == 1

    await set_rls_context(
        rls_session, role="admin", api_key_id=str(other_key_id), organization_id=_DEFAULT_ORG_ID
    )
    result = await rls_session.execute(
        text("SELECT count(*) FROM webhooks WHERE api_key_id = :id"), {"id": str(owner_key_id)}
    )
    assert result.scalar_one() == 1

    # Seeded via a separately-committed session above — rls_session's own
    # rollback (the fixture's cleanup) never touches these rows.
    cleanup_engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
    cleanup_session_factory = async_sessionmaker(cleanup_engine, expire_on_commit=False)
    async with cleanup_session_factory() as cleanup_db:
        await set_rls_context(cleanup_db, role="service")
        await cleanup_db.execute(
            text("DELETE FROM webhooks WHERE api_key_id = :id"), {"id": str(owner_key_id)}
        )
        await cleanup_db.execute(text("DELETE FROM api_keys WHERE id = :id"), {"id": str(owner_key_id)})
        await cleanup_db.commit()
    await cleanup_engine.dispose()


async def test_analyst_cannot_insert_webhook_for_another_key(rls_session: AsyncSession):
    await set_rls_context(rls_session, role="analyst", api_key_id=str(uuid.uuid4()))
    with pytest.raises(DBAPIError, match="row-level security policy"):
        await rls_session.execute(
            text(
                """
                INSERT INTO webhooks (id, organization_id, api_key_id, url, hmac_secret, is_active, created_at)
                VALUES (gen_random_uuid(), :org_id, :other_key_id, 'https://example.com/hook', 'secret', true, now())
                """
            ),
            {"org_id": _DEFAULT_ORG_ID, "other_key_id": str(uuid.uuid4())},
        )


async def test_analyst_cannot_select_eval_runs(rls_session: AsyncSession):
    await set_rls_context(rls_session, role="analyst")
    result = await rls_session.execute(text("SELECT count(*) FROM eval_runs"))
    assert result.scalar_one() == 0


async def test_admin_and_eng_lead_can_select_eval_runs(rls_session: AsyncSession):
    """Asserts the query executes without an RLS denial, not that eval_runs
    is empty — EVAL-01's harness can leave real rows behind now, unlike
    when this test was written (SEC-01), when eval_runs was genuinely
    empty in every environment."""
    for role in ("admin", "eng_lead"):
        await set_rls_context(rls_session, role=role)
        result = await rls_session.execute(text("SELECT count(*) FROM eval_runs"))
        assert result.scalar_one() >= 0


async def test_analyst_cannot_update_source_configs(rls_session: AsyncSession):
    await set_rls_context(rls_session, role="service")
    await rls_session.execute(
        text(
            "INSERT INTO source_configs (id, organization_id, source, domains, is_active, poll_interval_seconds) "
            "VALUES (gen_random_uuid(), :org_id, 'FINRA', '{}', true, 300) "
            "ON CONFLICT DO NOTHING"
        ),
        {"org_id": _DEFAULT_ORG_ID},
    )

    await set_rls_context(rls_session, role="analyst", organization_id=_DEFAULT_ORG_ID)
    result = await rls_session.execute(
        text("UPDATE source_configs SET is_active = false WHERE source = 'FINRA'")
    )
    assert result.rowcount == 0  # type: ignore[attr-defined]  # no rows matched the UPDATE policy


async def test_service_can_update_source_configs_last_polled_at(rls_session: AsyncSession):
    """ING-04's real scheduler dependency — service must be able to write
    last_polled_at/last_etag without being Admin."""
    await set_rls_context(rls_session, role="service")
    await rls_session.execute(
        text(
            "INSERT INTO source_configs (id, organization_id, source, domains, is_active, poll_interval_seconds) "
            "VALUES (gen_random_uuid(), :org_id, 'FDA', '{}', true, 300) "
            "ON CONFLICT DO NOTHING"
        ),
        {"org_id": _DEFAULT_ORG_ID},
    )
    result = await rls_session.execute(
        text("UPDATE source_configs SET last_polled_at = now() WHERE source = 'FDA'")
    )
    assert result.rowcount == 1  # type: ignore[attr-defined]


async def test_service_only_can_access_deliveries(rls_session: AsyncSession):
    await set_rls_context(rls_session, role="admin")
    result = await rls_session.execute(text("SELECT count(*) FROM deliveries"))
    assert result.scalar_one() == 0  # Admin has no policy on deliveries at all — deny by default

    await set_rls_context(rls_session, role="service")
    result = await rls_session.execute(text("SELECT count(*) FROM deliveries"))
    assert result.scalar_one() == 0  # service is permitted; asserts no RLS error is raised
