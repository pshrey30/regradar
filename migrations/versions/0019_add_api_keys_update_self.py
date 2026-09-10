"""User profile — let a caller UPDATE their own api_keys row (name,
password), not just Admin/service.

SEC-01's `api_keys_write` policy (0009, org-scoped by 0010) restricts every
UPDATE on `api_keys` to Admin or service — correct for role/is_active/
rate_limit changes (an ordinary user must never grant themselves a higher
role), but it also silently blocks the self-service "change my display
name" / "change my password" routes this ticket adds: a non-admin caller's
UPDATE would match zero rows under RLS, not error, so without this policy
those endpoints would look like they succeeded while writing nothing.

This policy is scoped by row identity only (`id = the caller's own key
id`), matching 0017's `deliveries_select_own_webhook` precedent for a
narrowly-scoped additive policy. It does not, and structurally cannot,
restrict which *columns* a matching UPDATE touches — Postgres RLS gates
rows, not columns — so the actual guarantee that a caller can only change
their own name/password (never their own role) lives in the application
code (`PATCH /v1/me`, `POST /v1/auth/change-password`), the same trust
boundary `api_keys_update_last_used` already relies on for `last_used_at`.

Additive: Postgres ORs multiple permissive policies for the same command,
so `api_keys_write`'s existing Admin/service UPDATE access is untouched.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-10
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_OWN_ROW = "id::text = current_setting('app.current_api_key_id', true)"


def upgrade() -> None:
    op.execute(f"CREATE POLICY api_keys_update_self ON api_keys FOR UPDATE USING ({_OWN_ROW})")


def downgrade() -> None:
    op.execute("DROP POLICY api_keys_update_self ON api_keys")
