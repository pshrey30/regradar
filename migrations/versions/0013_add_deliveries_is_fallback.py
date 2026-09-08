"""DELIV-04 — is_fallback flag on deliveries.

Distinguishes an email sent because Slack failed/wasn't configured for a
Critical/High filing from a normally-configured email delivery, per this
ticket's own acceptance criteria.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-08
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE deliveries ADD COLUMN is_fallback BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE deliveries DROP COLUMN is_fallback")
