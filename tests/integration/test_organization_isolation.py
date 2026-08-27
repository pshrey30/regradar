"""SEC-05 — proves organization-scoping is enforced by Postgres itself, not
application code: a second organization's data is genuinely invisible to
the first, even to an Admin key.

No organization-management surface exists in this app (deliberately out of
scope for this ticket) — this test creates its second organization and key
directly via SQL, the only place in the codebase a second organization is
ever created.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from regradar.core.config import get_settings
from regradar.core.db import set_rls_context
from regradar.ingestion.sources._common import insert_new_filing
from regradar.ingestion.types import NewFiling
from regradar.models.enums import FilingSource
from regradar.models.filing import Filing

pytestmark = pytest.mark.asyncio

_DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _load_real_env_for_settings(monkeypatch: pytest.MonkeyPatch):
    """Same pattern as test_row_level_security.py — see that file's
    fixture docstring for why this is needed."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    for key, value in dotenv_values(env_path).items():
        if value is not None:
            monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def second_org_with_data():
    """Creates a real second organization, an Admin key inside it, and a
    real filing/webhook belonging to it — all via direct SQL (no
    organization-management surface exists in the app itself), in a
    committed transaction so later test transactions can see it via MVCC.
    Cleaned up afterward.
    """
    settings = get_settings()
    engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    org_id = uuid.uuid4()
    admin_key_id = uuid.uuid4()
    filing_id = uuid.uuid4()
    webhook_id = uuid.uuid4()

    async with session_factory() as db:
        await set_rls_context(db, role="service")
        await db.execute(
            text("INSERT INTO organizations (id, name) VALUES (:id, 'Second Test Org')"),
            {"id": str(org_id)},
        )
        await db.execute(
            text(
                """
                INSERT INTO api_keys (id, organization_id, key_hash, owner_label, role,
                    is_active, rate_limit_per_minute, created_at)
                VALUES (:id, :org_id, :key_hash, 'org2-admin', 'admin', true, 60, now())
                """
            ),
            {"id": str(admin_key_id), "org_id": str(org_id), "key_hash": f"org2-hash-{admin_key_id}"},
        )
        await db.execute(
            text(
                """
                INSERT INTO filings (id, organization_id, source, source_document_id, entity_name,
                    filing_type, filing_url, status, published_at, ingested_at, created_at, updated_at)
                VALUES (:id, :org_id, 'SEC', :doc_id, 'Org2 Secret Corp', '10-K',
                    'http://example.com/org2', 'ingested', now(), now(), now(), now())
                """
            ),
            {"id": str(filing_id), "org_id": str(org_id), "doc_id": f"org2-doc-{filing_id}"},
        )
        await db.execute(
            text(
                """
                INSERT INTO webhooks (id, organization_id, api_key_id, url, hmac_secret,
                    is_active, created_at)
                VALUES (:id, :org_id, :key_id, 'https://example.com/org2-hook', 'secret', true, now())
                """
            ),
            {"id": str(webhook_id), "org_id": str(org_id), "key_id": str(admin_key_id)},
        )
        await db.commit()
    await engine.dispose()

    yield {"org_id": org_id, "admin_key_id": admin_key_id, "filing_id": filing_id, "webhook_id": webhook_id}

    async with session_factory() as db:
        await set_rls_context(db, role="service")
        await db.execute(text("DELETE FROM webhooks WHERE organization_id = :id"), {"id": str(org_id)})
        await db.execute(text("DELETE FROM filings WHERE organization_id = :id"), {"id": str(org_id)})
        await db.execute(text("DELETE FROM api_keys WHERE organization_id = :id"), {"id": str(org_id)})
        await db.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": str(org_id)})
        await db.commit()
    await engine.dispose()


async def test_org_1_admin_cannot_see_org_2s_filing(second_org_with_data):
    settings = get_settings()
    engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        # A real, distinct Admin key in the DEFAULT (org 1) organization —
        # not the same key second_org_with_data created in org 2.
        await set_rls_context(db, role="service")
        org1_admin_key_id = uuid.uuid4()
        await db.execute(
            text(
                """
                INSERT INTO api_keys (id, organization_id, key_hash, owner_label, role,
                    is_active, rate_limit_per_minute, created_at)
                VALUES (:id, :org_id, :key_hash, 'org1-admin', 'admin', true, 60, now())
                """
            ),
            {
                "id": str(org1_admin_key_id),
                "org_id": _DEFAULT_ORG_ID,
                "key_hash": f"org1-hash-{org1_admin_key_id}",
            },
        )
        await db.commit()

        try:
            await set_rls_context(
                db,
                role="admin",
                api_key_id=str(org1_admin_key_id),
                organization_id=_DEFAULT_ORG_ID,
            )
            result = await db.execute(
                text("SELECT count(*) FROM filings WHERE id = :id"),
                {"id": str(second_org_with_data["filing_id"])},
            )
            assert result.scalar_one() == 0, (
                "org 1's Admin can see org 2's filing — organization-scoping is broken"
            )

            result = await db.execute(
                text("SELECT count(*) FROM webhooks WHERE id = :id"),
                {"id": str(second_org_with_data["webhook_id"])},
            )
            assert result.scalar_one() == 0, (
                "org 1's Admin can see org 2's webhook — organization-scoping is broken"
            )
        finally:
            await set_rls_context(db, role="service")
            await db.execute(text("DELETE FROM api_keys WHERE id = :id"), {"id": str(org1_admin_key_id)})
            await db.commit()
    await engine.dispose()


async def test_org_2s_own_admin_can_see_its_own_filing(second_org_with_data):
    settings = get_settings()
    engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        await set_rls_context(
            db,
            role="admin",
            api_key_id=str(second_org_with_data["admin_key_id"]),
            organization_id=str(second_org_with_data["org_id"]),
        )
        result = await db.execute(
            text("SELECT count(*) FROM filings WHERE id = :id"),
            {"id": str(second_org_with_data["filing_id"])},
        )
        assert result.scalar_one() == 1
    await engine.dispose()


async def test_default_org_filings_stay_invisible_to_org_2(second_org_with_data):
    """The reverse direction — org 2 must not see org 1's (pre-existing,
    real) filings either. Uses a real committed filing in the default org
    so this isn't just an empty-result coincidence."""
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
                    filing_type, filing_url, status, published_at, ingested_at, created_at, updated_at)
                VALUES (:id, :org_id, 'SEC', :doc_id, 'Org1 Real Corp', '10-K',
                    'http://example.com/org1', 'ingested', :now, :now, :now, :now)
                """
            ),
            {
                "id": str(filing_id),
                "org_id": _DEFAULT_ORG_ID,
                "doc_id": f"org1-doc-{filing_id}",
                "now": datetime.now(UTC),
            },
        )
        await db.commit()

        try:
            await set_rls_context(
                db,
                role="admin",
                api_key_id=str(second_org_with_data["admin_key_id"]),
                organization_id=str(second_org_with_data["org_id"]),
            )
            result = await db.execute(
                text("SELECT count(*) FROM filings WHERE id = :id"), {"id": str(filing_id)}
            )
            assert result.scalar_one() == 0, (
                "org 2's Admin can see org 1's filing — organization-scoping is broken"
            )
        finally:
            await set_rls_context(db, role="service")
            await db.execute(text("DELETE FROM filings WHERE id = :id"), {"id": str(filing_id)})
            await db.commit()
    await engine.dispose()


async def test_two_orgs_can_each_ingest_the_same_public_document():
    """Real bug found in a post-SEC-05 audit: `uq_filings_source_document_id`
    was `UNIQUE(source, source_document_id)` only — organization_id wasn't
    part of it, unchanged since migration 0002, before organization_id
    existed on filings at all. Two organizations both monitoring the same
    public source that discover the same document would only ever get ONE
    of them a Filing row: the second organization's insert_new_filing call
    hit this constraint, caught the resulting IntegrityError (SEC-04's own
    duplicate-race handling), and returned None — indistinguishable from a
    genuine same-org race loss, silently dropping that org's filing.

    Migration 0011 widened the constraint to (organization_id, source,
    source_document_id); this proves two distinct orgs can each get their
    own row for the exact same document.
    """
    settings = get_settings()
    engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    org_id = uuid.uuid4()
    doc_id = f"cross-org-doc-{uuid.uuid4()}"

    async with session_factory() as db:
        await set_rls_context(db, role="service")
        await db.execute(
            text("INSERT INTO organizations (id, name) VALUES (:id, 'Cross-Org Doc Test Org')"),
            {"id": str(org_id)},
        )
        await db.commit()
    await engine.dispose()

    async def _insert(organization_id: uuid.UUID):
        engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            await set_rls_context(db, role="service")
            source_config = SimpleNamespace(source=FilingSource.SEC, organization_id=organization_id)
            result = await insert_new_filing(
                db,
                source_config,
                NewFiling(
                    source_document_id=doc_id,
                    entity_name="Cross-Org Doc Test Corp",
                    filing_type="10-K",
                    filing_url="https://example.com/cross-org-doc-test",
                    published_at=datetime.now(UTC),
                ),
            )
            await db.commit()
        await engine.dispose()
        return result

    try:
        default_org_result = await _insert(uuid.UUID(_DEFAULT_ORG_ID))
        second_org_result = await _insert(org_id)

        assert default_org_result is not None, "default org's insert unexpectedly failed"
        assert second_org_result is not None, (
            "second org's insert was rejected by the (source, source_document_id) unique "
            "constraint — organization_id must be part of that constraint"
        )

        engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            await set_rls_context(db, role="service")
            rows = (
                await db.execute(
                    select(Filing).where(
                        Filing.source == FilingSource.SEC, Filing.source_document_id == doc_id
                    )
                )
            ).scalars().all()
            assert {row.organization_id for row in rows} == {uuid.UUID(_DEFAULT_ORG_ID), org_id}
            await db.execute(
                delete(Filing).where(
                    Filing.source == FilingSource.SEC, Filing.source_document_id == doc_id
                )
            )
            await db.commit()
        await engine.dispose()
    finally:
        engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            await set_rls_context(db, role="service")
            await db.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": str(org_id)})
            await db.commit()
        await engine.dispose()
