"""FE-07 — a non-sensitive display fragment for the API Keys list screen.

`api_keys.key_hash` alone can't produce "shown masked, e.g. last 4
characters" the ticket calls for — it's a one-way SHA-256 digest of the
plaintext key, not derivable back into a suffix of the original string.
`key_suffix` stores just the last 4 characters of the plaintext at
creation time, alongside the hash (never instead of it — key_hash is
still what authentication actually checks). 4 characters of a
32-byte-random key carries essentially no brute-force value on its own;
it exists purely so an Admin can recognize *which* key is which in a
list without ever seeing the full value again.

Nullable: existing keys minted via the CLI before this ticket have no
suffix on record and display as a placeholder instead of backfilling a
value that was never captured.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-10
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE api_keys ADD COLUMN key_suffix TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE api_keys DROP COLUMN key_suffix")
