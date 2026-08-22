"""Add needs_review to the filing_status enum.

AGENT-07's Analysis Agent sets this status when structured extraction
fails validation twice (malformed JSON, missing schema fields, or an
out-of-range source_chunk_index), instead of saving incomplete data.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-22
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

NEW_VALUE = "needs_review"
ORIGINAL_VALUES = [
    "ingested",
    "classifying",
    "needs_classification",
    "retrieving",
    "analyzing",
    "summarizing",
    "delivering",
    "complete",
    "failed",
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE filing_status ADD VALUE '{NEW_VALUE}'")


def downgrade() -> None:
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
