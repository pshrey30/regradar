"""Add needs_classification to the filing_status enum.

AGENT-02's Triage Agent sets this status when HF classification fails
after a retry, instead of guessing a domain/risk_level.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-12
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

NEW_VALUE = "needs_classification"
ORIGINAL_VALUES = [
    "ingested",
    "classifying",
    "retrieving",
    "analyzing",
    "summarizing",
    "delivering",
    "complete",
    "failed",
]


def upgrade() -> None:
    # Postgres requires ALTER TYPE ... ADD VALUE to run outside an explicit
    # transaction.
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE filing_status ADD VALUE '{NEW_VALUE}'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE. Standard workaround:
    # reassign any rows using the value, then recreate the type without it.
    # The column's server_default must be dropped before the type change —
    # Postgres can't auto-cast a column's DEFAULT expression to the new
    # enum type in the same ALTER — and restored after.
    op.execute(f"UPDATE filings SET status = 'failed' WHERE status = '{NEW_VALUE}'")
    values_sql = ", ".join(f"'{v}'" for v in ORIGINAL_VALUES)
    op.execute(f"CREATE TYPE filing_status_old AS ENUM ({values_sql})")
    op.execute("ALTER TABLE filings ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE filings ALTER COLUMN status TYPE filing_status_old "
        "USING status::text::filing_status_old"
    )
    op.execute("DROP TYPE filing_status")
    op.execute("ALTER TYPE filing_status_old RENAME TO filing_status")
    op.execute("ALTER TABLE filings ALTER COLUMN status SET DEFAULT 'ingested'")
