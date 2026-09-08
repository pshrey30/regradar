"""FE-02 — email/password signup columns on api_keys.

Alongside Google SSO, an api_keys row can now be created and authenticated
via a plain email/password pair — email is the lookup key (a real, salted
bcrypt hash, unlike key_hash's fast SHA-256, which is correct only for a
high-entropy random API key, not a low-entropy user-chosen password).

A partial unique index on email, matching migration 0014's sso identity
index — most rows (programmatically-issued keys, SSO-only rows) have it
NULL, and a plain UNIQUE would work too (Postgres treats NULLs as
distinct), but the partial form makes "only enforced when actually set"
explicit rather than incidental, and doubles as a real defense: it's what
stops signup from creating two rows for the same email.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-08
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE api_keys ADD COLUMN email TEXT")
    op.execute("ALTER TABLE api_keys ADD COLUMN password_hash TEXT")
    op.execute(
        "CREATE UNIQUE INDEX uq_api_keys_email ON api_keys (email) WHERE email IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_api_keys_email")
    op.execute("ALTER TABLE api_keys DROP COLUMN password_hash")
    op.execute("ALTER TABLE api_keys DROP COLUMN email")
