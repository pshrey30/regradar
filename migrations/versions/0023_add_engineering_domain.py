"""Add engineering to the filing_domain enum.

Role-scoped dashboards: Eng Lead's dashboard is restricted to Engineering-
domain filings, so the domain has to actually exist to classify into. The
triage agent's candidate labels (HF zero-shot + the LLM spot-check prompt)
are updated alongside this to include it.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-16
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

NEW_VALUE = "engineering"
ORIGINAL_VALUES = ["financial", "clinical", "environmental", "other"]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE filing_domain ADD VALUE '{NEW_VALUE}'")


def downgrade() -> None:
    op.execute(f"UPDATE filings SET domain = NULL WHERE domain = '{NEW_VALUE}'")
    values_sql = ", ".join(f"'{v}'" for v in ORIGINAL_VALUES)
    op.execute(f"CREATE TYPE filing_domain_old AS ENUM ({values_sql})")
    op.execute(
        "ALTER TABLE filings ALTER COLUMN domain TYPE filing_domain_old "
        "USING domain::text::filing_domain_old"
    )
    op.execute("DROP TYPE filing_domain")
    op.execute("ALTER TYPE filing_domain_old RENAME TO filing_domain")
