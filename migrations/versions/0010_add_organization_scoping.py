"""SEC-05 — organization-scoping scaffolding.

Adds a minimal `organizations` table and `organization_id` to the five
tables that own organization identity directly (filings, webhooks,
deliveries, source_configs, api_keys), then extends every SEC-01 RLS
policy on those tables — plus filing_chunks/extractions/briefs, which
join through `filings.organization_id` rather than duplicating the
column — with an additional, ANDed organization check.

`service` (workers/CLI) continues to bypass org-scoping the same way it
already bypasses role-scoping — one shared pipeline, not one per org.
Admin becomes org-scoped, not a cross-org super-admin: nothing in this
ticket calls for a cross-org admin capability. `eval_runs` and the
`api_keys_select`/`api_keys_update_last_used` policies are untouched —
see the design spec (docs/superpowers/specs/2026-08-26-sec-05-org-
scoping-design.md) for why.

Org comparisons are done as text (`organization_id::text = current_setting(
'app.current_organization_id', true)`), never cast to uuid — the same
cast-safety reasoning 0009 already established for `_OWN_KEY`: Postgres
doesn't guarantee OR short-circuit evaluation order, so a service actor
(whose org GUC is unset/empty) could otherwise hit an invalid-uuid-cast
error depending on plan order.

A single default organization is seeded with a fixed, well-known UUID
(not `gen_random_uuid()`) so it can be referenced by exact value in the
backfill statements below, in later tests, and by any operator inspecting
the database directly.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-26
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
_ORG_SCOPED_TABLES = ["filings", "webhooks", "deliveries", "source_configs", "api_keys"]

_ORG_MATCH = "organization_id::text = current_setting('app.current_organization_id', true)"
_IS_SERVICE = "current_setting('app.current_role', true) = 'service'"
_IS_ADMIN = "current_setting('app.current_role', true) = 'admin'"
_IS_EXECUTIVE = "current_setting('app.current_role', true) = 'executive'"
_AUTHENTICATED = (
    "current_setting('app.current_role', true) "
    "IN ('admin', 'analyst', 'executive', 'legal_counsel', 'eng_lead', 'service')"
)
_OWN_KEY = "api_key_id::text = current_setting('app.current_api_key_id', true)"


def _filing_org_match(child_table: str) -> str:
    """child_table joins to filings by filing_id; matches on the parent
    filing's organization_id, same text-comparison safety as _ORG_MATCH."""
    return (
        f"EXISTS (SELECT 1 FROM filings f WHERE f.id = {child_table}.filing_id "
        f"AND f.organization_id::text = current_setting('app.current_organization_id', true))"
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE organizations (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"INSERT INTO organizations (id, name) VALUES ('{_DEFAULT_ORG_ID}', 'Default Organization')"
    )

    for table in _ORG_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ADD COLUMN organization_id UUID")
        op.execute(f"UPDATE {table} SET organization_id = '{_DEFAULT_ORG_ID}' WHERE organization_id IS NULL")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN organization_id SET NOT NULL")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_organization_id "
            f"FOREIGN KEY (organization_id) REFERENCES organizations(id)"
        )

    # organizations itself: readable by any authenticated caller (a caller
    # needs to resolve its own org's name eventually — GET /v1/me returns
    # the id only for now, but there's no reason to lock this table down
    # tighter than that); writes are service-only (no creation surface
    # exists yet, deliberately, per this ticket's scope).
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY organizations_select ON organizations FOR SELECT USING ({_AUTHENTICATED})")
    op.execute(
        f"CREATE POLICY organizations_write ON organizations FOR ALL "
        f"USING ({_IS_SERVICE}) WITH CHECK ({_IS_SERVICE})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON organizations TO regradar_app")

    # filings — SELECT now also requires the caller's own org (service still
    # bypasses entirely); write stays service-only, unaffected.
    op.execute("DROP POLICY filings_select ON filings")
    op.execute(
        f"CREATE POLICY filings_select ON filings FOR SELECT "
        f"USING (({_AUTHENTICATED} AND {_ORG_MATCH}) OR {_IS_SERVICE})"
    )

    # filing_chunks / extractions / briefs — join to filings for org match;
    # write policies stay service-only, unaffected.
    op.execute("DROP POLICY filing_chunks_select ON filing_chunks")
    op.execute(
        f"CREATE POLICY filing_chunks_select ON filing_chunks FOR SELECT "
        f"USING (({_AUTHENTICATED} AND {_filing_org_match('filing_chunks')}) OR {_IS_SERVICE})"
    )
    op.execute("DROP POLICY extractions_select ON extractions")
    op.execute(
        f"CREATE POLICY extractions_select ON extractions FOR SELECT "
        f"USING (({_AUTHENTICATED} AND NOT ({_IS_EXECUTIVE}) AND {_filing_org_match('extractions')}) "
        f"OR {_IS_SERVICE})"
    )
    op.execute("DROP POLICY briefs_select ON briefs")
    op.execute(
        f"CREATE POLICY briefs_select ON briefs FOR SELECT "
        f"USING (({_AUTHENTICATED} AND {_filing_org_match('briefs')}) OR {_IS_SERVICE})"
    )

    # webhooks — _OWN_KEY alone stays sufficient for a non-admin caller (a
    # key's own webhooks were only ever created within that key's own org,
    # enforced by the INSERT policy below); Admin's branch becomes
    # org-scoped instead of global.
    webhooks_check = f"({_OWN_KEY} OR {_IS_SERVICE} OR ({_IS_ADMIN} AND {_ORG_MATCH}))"
    for policy, cmd in [
        ("webhooks_select", "SELECT"),
        ("webhooks_update", "UPDATE"),
        ("webhooks_delete", "DELETE"),
    ]:
        op.execute(f"DROP POLICY {policy} ON webhooks")
        op.execute(f"CREATE POLICY {policy} ON webhooks FOR {cmd} USING {webhooks_check}")
    op.execute("DROP POLICY webhooks_insert ON webhooks")
    op.execute(f"CREATE POLICY webhooks_insert ON webhooks FOR INSERT WITH CHECK {webhooks_check}")

    # source_configs — SELECT becomes org-scoped (previously any
    # authenticated role saw every source_configs row); Admin's write
    # branch becomes org-scoped, service unaffected.
    op.execute("DROP POLICY source_configs_select ON source_configs")
    op.execute(
        f"CREATE POLICY source_configs_select ON source_configs FOR SELECT "
        f"USING (({_AUTHENTICATED} AND {_ORG_MATCH}) OR {_IS_SERVICE})"
    )
    source_configs_write_check = f"(({_IS_ADMIN} AND {_ORG_MATCH}) OR {_IS_SERVICE})"
    for policy, cmd in [
        ("source_configs_insert", "INSERT"),
        ("source_configs_update", "UPDATE"),
        ("source_configs_delete", "DELETE"),
    ]:
        op.execute(f"DROP POLICY {policy} ON source_configs")
        clause = "WITH CHECK" if cmd == "INSERT" else "USING"
        op.execute(
            f"CREATE POLICY {policy} ON source_configs FOR {cmd} {clause} {source_configs_write_check}"
        )

    # api_keys — api_keys_select and api_keys_update_last_used are untouched
    # (see module docstring); api_keys_write's Admin branch becomes
    # org-scoped, service unaffected.
    op.execute("DROP POLICY api_keys_write ON api_keys")
    api_keys_write_check = f"(({_IS_ADMIN} AND {_ORG_MATCH}) OR {_IS_SERVICE})"
    op.execute(
        f"CREATE POLICY api_keys_write ON api_keys FOR ALL "
        f"USING {api_keys_write_check} WITH CHECK {api_keys_write_check}"
    )

    # deliveries — no customer-facing role reads this table at all yet
    # (unchanged from 0009); left as-is, service-only.


def downgrade() -> None:
    op.execute("DROP POLICY api_keys_write ON api_keys")
    op.execute(
        "CREATE POLICY api_keys_write ON api_keys FOR ALL "
        f"USING (({_IS_ADMIN} OR {_IS_SERVICE})) WITH CHECK (({_IS_ADMIN} OR {_IS_SERVICE}))"
    )

    for policy in ["source_configs_insert", "source_configs_update", "source_configs_delete"]:
        op.execute(f"DROP POLICY {policy} ON source_configs")
    op.execute(
        "CREATE POLICY source_configs_insert ON source_configs FOR INSERT "
        f"WITH CHECK (({_IS_ADMIN} OR {_IS_SERVICE}))"
    )
    op.execute(
        "CREATE POLICY source_configs_update ON source_configs FOR UPDATE "
        f"USING (({_IS_ADMIN} OR {_IS_SERVICE}))"
    )
    op.execute(
        "CREATE POLICY source_configs_delete ON source_configs FOR DELETE "
        f"USING (({_IS_ADMIN} OR {_IS_SERVICE}))"
    )
    op.execute("DROP POLICY source_configs_select ON source_configs")
    op.execute(f"CREATE POLICY source_configs_select ON source_configs FOR SELECT USING ({_AUTHENTICATED})")

    for policy, cmd in [
        ("webhooks_select", "SELECT"),
        ("webhooks_update", "UPDATE"),
        ("webhooks_delete", "DELETE"),
    ]:
        op.execute(f"DROP POLICY {policy} ON webhooks")
        op.execute(
            f"CREATE POLICY {policy} ON webhooks FOR {cmd} USING (({_OWN_KEY} OR {_IS_ADMIN} OR {_IS_SERVICE}))"
        )
    op.execute("DROP POLICY webhooks_insert ON webhooks")
    op.execute(
        "CREATE POLICY webhooks_insert ON webhooks FOR INSERT "
        f"WITH CHECK (({_OWN_KEY} OR {_IS_ADMIN} OR {_IS_SERVICE}))"
    )

    op.execute("DROP POLICY briefs_select ON briefs")
    op.execute(f"CREATE POLICY briefs_select ON briefs FOR SELECT USING ({_AUTHENTICATED})")
    op.execute("DROP POLICY extractions_select ON extractions")
    op.execute(
        f"CREATE POLICY extractions_select ON extractions FOR SELECT USING ({_AUTHENTICATED} AND NOT ({_IS_EXECUTIVE}))"
    )
    op.execute("DROP POLICY filing_chunks_select ON filing_chunks")
    op.execute(f"CREATE POLICY filing_chunks_select ON filing_chunks FOR SELECT USING ({_AUTHENTICATED})")
    op.execute("DROP POLICY filings_select ON filings")
    op.execute(f"CREATE POLICY filings_select ON filings FOR SELECT USING ({_AUTHENTICATED})")

    op.execute("DROP POLICY organizations_select ON organizations")
    op.execute("DROP POLICY organizations_write ON organizations")
    op.execute("ALTER TABLE organizations NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations DISABLE ROW LEVEL SECURITY")

    for table in reversed(_ORG_SCOPED_TABLES):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT fk_{table}_organization_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN organization_id")

    op.execute("DROP TABLE organizations")
