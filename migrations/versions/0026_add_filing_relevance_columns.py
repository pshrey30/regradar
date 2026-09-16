"""ORG-11 — org-relevance explanation columns on filings.

Flat columns, matching how domain/risk_level/priority_score/
classification_confidence already live directly on Filing rather than a
side table (small, 1:1 data, always read alongside the rest of the row —
unlike Extraction/Brief, which are large enough to warrant their own
tables).

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-16
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE filings ADD COLUMN relevance_rationale TEXT")
    op.execute("ALTER TABLE filings ADD COLUMN recommended_action TEXT")
    op.execute("ALTER TABLE filings ADD COLUMN matched_signals JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE filings DROP COLUMN matched_signals")
    op.execute("ALTER TABLE filings DROP COLUMN recommended_action")
    op.execute("ALTER TABLE filings DROP COLUMN relevance_rationale")
