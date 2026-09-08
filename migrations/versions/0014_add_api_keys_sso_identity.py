"""FE-02 — SSO identity columns on api_keys.

A Google-authenticated session resolves to an ApiKey row exactly like a
directly-issued key does (per API-11's own docstring, written ahead of
this ticket) — the same key_hash lookup, same RLS policies, same rate
limiting. sso_provider/sso_subject_id let the callback find-or-create
that row from a stable OIDC identity (Google's `sub` claim, not email —
email can change, sub can't) rather than a plaintext key the browser
never has to see.

A partial unique index (not a plain UNIQUE constraint) is used because
most api_keys rows have both columns NULL — programmatically-issued keys
never had an SSO login — and Postgres's plain UNIQUE treats every NULL
as distinct anyway, but a partial index makes that "only enforced when
both are actually set" behavior explicit rather than incidental.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-08
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE api_keys ADD COLUMN sso_provider TEXT")
    op.execute("ALTER TABLE api_keys ADD COLUMN sso_subject_id TEXT")
    op.execute(
        "CREATE UNIQUE INDEX uq_api_keys_sso_identity ON api_keys (sso_provider, sso_subject_id) "
        "WHERE sso_provider IS NOT NULL AND sso_subject_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_api_keys_sso_identity")
    op.execute("ALTER TABLE api_keys DROP COLUMN sso_subject_id")
    op.execute("ALTER TABLE api_keys DROP COLUMN sso_provider")
