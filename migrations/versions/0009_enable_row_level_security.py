"""Enable row-level security on every table, deny-by-default.

The app and every worker/CLI process connect to Postgres as the same role
(`regradar`) that also owns every table — Postgres exempts a table's owner
from its own RLS policies by default, which would make this migration a
silent no-op for our own connections. `FORCE ROW LEVEL SECURITY` on every
table closes that: there is no dedicated non-owner "app" login role in this
single-tenant deployment, so forcing is the only way these policies bind.

The caller's identity is threaded through two session-scoped Postgres GUCs
(`app.current_role`, `app.current_api_key_id`), set once per request/task
via `core.db.set_rls_context()` on the exact connection that will run the
caller's real queries — read here with `current_setting(name, true)`, whose
`missing_ok=true` returns NULL (not an error) for any connection that never
called it (e.g. a raw `psql` session), so an unset GUC always denies rather
than raising.

Two real gaps in the ticket's own literal policy list, found by tracing
actual read/write call sites in this codebase rather than assumed:
- `filing_chunks` needs a SELECT policy for authenticated callers too —
  API-06's search endpoint reads it directly on the request's own
  connection. The ticket's policy list only mentions its write restriction.
- `source_configs` needs UPDATE for the `service` actor, not just `admin` —
  ING-04's `poll_all_sources` flow updates `last_polled_at`/`last_etag`
  after every scheduled poll, running as `service`, never as an API caller.

`api_keys` SELECT is intentionally unconditional (`USING (true)`): the
lookup that resolves a caller's role from a bearer token is itself the
query that would need `app.current_role` already set to pass a
role-gated policy — a chicken-and-egg the ticket doesn't address. Nothing
in this app exposes api_keys rows to a caller beyond their own resolved
identity (there is no key-listing endpoint), so this is safe. Writes to
api_keys (key creation/revocation) stay restricted to `service`/`admin`.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-26
"""

import os

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_ALL_TABLES = [
    "filings",
    "filing_chunks",
    "extractions",
    "briefs",
    "webhooks",
    "deliveries",
    "source_configs",
    "eval_runs",
    "api_keys",
]

# Membership checks against a known-role allowlist, never NULL-checks: a
# transaction-local set_config('x', val, true) does NOT revert to NULL once
# its transaction ends on a pooled connection that has ever touched that
# GUC before — it reverts to '' (empty string), a real, observed Postgres
# behavior for custom run-time parameters, verified directly against this
# project's actual connection pool before writing these policies this way.
# `current_setting(..., true) IS NOT NULL` would therefore treat ANY
# previously-used pooled connection as "authenticated" even when the
# current transaction never set a role at all — silently defeating
# deny-by-default. An explicit allowlist is correct regardless of what a
# touched-but-unset GUC reverts to, since neither NULL nor '' is ever a
# member of it.
_KNOWN_ROLES = "('admin', 'analyst', 'executive', 'legal_counsel', 'eng_lead', 'service')"
_AUTHENTICATED = f"current_setting('app.current_role', true) IN {_KNOWN_ROLES}"
_IS_SERVICE = "current_setting('app.current_role', true) = 'service'"
_IS_ADMIN = "current_setting('app.current_role', true) = 'admin'"
_IS_EXECUTIVE = "current_setting('app.current_role', true) = 'executive'"
_IS_ADMIN_OR_EXEC = "current_setting('app.current_role', true) IN ('admin', 'eng_lead')"
# Compared as text, never cast to uuid: Postgres doesn't guarantee OR
# short-circuit evaluation order, so a service/admin actor (whose
# app.current_api_key_id GUC is unset or empty) could otherwise hit an
# "invalid input syntax for type uuid" error depending on plan order.
_OWN_KEY = "api_key_id::text = current_setting('app.current_api_key_id', true)"


_APP_ROLE = "regradar_app"


def upgrade() -> None:
    # A Postgres superuser (or BYPASSRLS role) ignores RLS entirely, FORCE
    # or not — the local dev role this project already connects as
    # (`regradar`) is exactly that. Provision a genuinely restricted role
    # every app/worker/CLI connection uses instead (core.config.Settings.
    # app_database_url); migrations keep running as the powerful role,
    # since DDL here needs owner/superuser privilege this new role won't
    # have. Idempotent — CREATE ROLE has no IF NOT EXISTS, so DO-block guard.
    password = os.environ.get("APP_DB_ROLE_PASSWORD", "regradar_app")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                CREATE ROLE {_APP_ROLE} WITH LOGIN NOSUPERUSER NOBYPASSRLS
                    NOCREATEDB NOCREATEROLE PASSWORD '{password}';
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(_ALL_TABLES)} TO {_APP_ROLE}"
    )

    for table in _ALL_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # filings — any authenticated caller (or service) reads; only service writes.
    op.execute(f"CREATE POLICY filings_select ON filings FOR SELECT USING ({_AUTHENTICATED})")
    op.execute(f"CREATE POLICY filings_write ON filings FOR ALL USING ({_IS_SERVICE}) WITH CHECK ({_IS_SERVICE})")

    # filing_chunks — same shape as filings; SELECT needed for API-06 search,
    # not named in the ticket's own policy list but required by a real caller.
    op.execute(
        f"CREATE POLICY filing_chunks_select ON filing_chunks FOR SELECT USING ({_AUTHENTICATED})"
    )
    op.execute(
        f"CREATE POLICY filing_chunks_write ON filing_chunks FOR ALL "
        f"USING ({_IS_SERVICE}) WITH CHECK ({_IS_SERVICE})"
    )

    # extractions — every authenticated role except Executive; only service writes.
    op.execute(
        f"CREATE POLICY extractions_select ON extractions FOR SELECT "
        f"USING ({_AUTHENTICATED} AND NOT ({_IS_EXECUTIVE}))"
    )
    op.execute(
        f"CREATE POLICY extractions_write ON extractions FOR ALL "
        f"USING ({_IS_SERVICE}) WITH CHECK ({_IS_SERVICE})"
    )

    # briefs — any authenticated role (Executive's persona narrowing is an
    # app-level concern, API-07 — not a row-visibility one); only service writes.
    op.execute(f"CREATE POLICY briefs_select ON briefs FOR SELECT USING ({_AUTHENTICATED})")
    op.execute(f"CREATE POLICY briefs_write ON briefs FOR ALL USING ({_IS_SERVICE}) WITH CHECK ({_IS_SERVICE})")

    # webhooks — a key only sees/manages its own rows, unless Admin (or service).
    own_or_admin_or_service = f"({_OWN_KEY} OR {_IS_ADMIN} OR {_IS_SERVICE})"
    op.execute(f"CREATE POLICY webhooks_select ON webhooks FOR SELECT USING {own_or_admin_or_service}")
    op.execute(
        f"CREATE POLICY webhooks_insert ON webhooks FOR INSERT WITH CHECK {own_or_admin_or_service}"
    )
    op.execute(f"CREATE POLICY webhooks_update ON webhooks FOR UPDATE USING {own_or_admin_or_service}")
    op.execute(f"CREATE POLICY webhooks_delete ON webhooks FOR DELETE USING {own_or_admin_or_service}")

    # deliveries — no API route reads this yet; only the service actor
    # (AGENT-10's deliver_node) touches it at all. Deny-by-default for
    # every other role is deliberate, not an oversight.
    op.execute(f"CREATE POLICY deliveries_service ON deliveries FOR ALL USING ({_IS_SERVICE}) WITH CHECK ({_IS_SERVICE})")

    # source_configs — SELECT for any authenticated role or service;
    # INSERT/DELETE for Admin or service; UPDATE also allowed for service
    # alone (ING-04's scheduler updates last_polled_at/last_etag as service,
    # never as an Admin API caller — a real gap in the ticket's literal policy).
    op.execute(
        f"CREATE POLICY source_configs_select ON source_configs FOR SELECT "
        f"USING ({_AUTHENTICATED})"
    )
    admin_or_service = f"({_IS_ADMIN} OR {_IS_SERVICE})"
    op.execute(
        f"CREATE POLICY source_configs_insert ON source_configs FOR INSERT WITH CHECK {admin_or_service}"
    )
    op.execute(
        f"CREATE POLICY source_configs_update ON source_configs FOR UPDATE USING {admin_or_service}"
    )
    op.execute(
        f"CREATE POLICY source_configs_delete ON source_configs FOR DELETE USING {admin_or_service}"
    )

    # eval_runs — SELECT restricted to Admin/Eng Lead (matches API-09's
    # app-level check, now enforced at the DB too); service writes for the
    # not-yet-built EVAL epic's harness jobs.
    op.execute(
        f"CREATE POLICY eval_runs_select ON eval_runs FOR SELECT "
        f"USING ({_IS_ADMIN_OR_EXEC} OR {_IS_SERVICE})"
    )
    op.execute(
        f"CREATE POLICY eval_runs_write ON eval_runs FOR ALL USING ({_IS_SERVICE}) WITH CHECK ({_IS_SERVICE})"
    )

    # api_keys — SELECT unconditional (see module docstring: auth lookup
    # itself needs this before app.current_role is known). Writes
    # (creation/revocation) restricted to service (the CLI) or Admin.
    op.execute("CREATE POLICY api_keys_select ON api_keys FOR SELECT USING (true)")
    op.execute(
        f"CREATE POLICY api_keys_write ON api_keys FOR ALL "
        f"USING {admin_or_service} WITH CHECK {admin_or_service}"
    )
    # The auth lookup itself (deps.get_current_key) also updates
    # last_used_at on every successful request, before it has resolved a
    # real role — deps.py explicitly tags that one session with the
    # 'auth_lookup' sentinel (never a real api-key role, never 'service')
    # so this narrow write can be permitted without relying on NULL-vs-''
    # GUC-reset semantics (see _AUTHENTICATED's comment above).
    op.execute(
        "CREATE POLICY api_keys_update_last_used ON api_keys FOR UPDATE "
        "USING (current_setting('app.current_role', true) = 'auth_lookup')"
    )


def downgrade() -> None:
    for table in reversed(_ALL_TABLES):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for policy, table in [
        ("filings_select", "filings"),
        ("filings_write", "filings"),
        ("filing_chunks_select", "filing_chunks"),
        ("filing_chunks_write", "filing_chunks"),
        ("extractions_select", "extractions"),
        ("extractions_write", "extractions"),
        ("briefs_select", "briefs"),
        ("briefs_write", "briefs"),
        ("webhooks_select", "webhooks"),
        ("webhooks_insert", "webhooks"),
        ("webhooks_update", "webhooks"),
        ("webhooks_delete", "webhooks"),
        ("deliveries_service", "deliveries"),
        ("source_configs_select", "source_configs"),
        ("source_configs_insert", "source_configs"),
        ("source_configs_update", "source_configs"),
        ("source_configs_delete", "source_configs"),
        ("eval_runs_select", "eval_runs"),
        ("eval_runs_write", "eval_runs"),
        ("api_keys_select", "api_keys"),
        ("api_keys_write", "api_keys"),
        ("api_keys_update_last_used", "api_keys"),
    ]:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                REVOKE ALL ON {", ".join(_ALL_TABLES)} FROM {_APP_ROLE};
                REVOKE USAGE ON SCHEMA public FROM {_APP_ROLE};
                DROP ROLE {_APP_ROLE};
            END IF;
        END
        $$;
        """
    )
