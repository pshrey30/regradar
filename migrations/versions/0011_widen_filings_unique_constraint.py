"""Widen filings' uq_filings_source_document_id to include organization_id.

Real bug found in a post-SEC-05 audit: `uq_filings_source_document_id` was
`UNIQUE(source, source_document_id)`, unchanged since migration 0002 —
before `organization_id` existed on `filings` at all. Two organizations
both monitoring the same public source (e.g. both watch SEC EDGAR) that
both discover the same document would only ever get ONE of them a Filing
row: the second organization's `insert_new_filing` call hits this
constraint, catches the resulting IntegrityError (SEC-04's own
duplicate-race handling), and silently returns None — indistinguishable
from a benign same-org race loss, so that organization permanently never
sees a filing it should have.

Widened to `UNIQUE(organization_id, source, source_document_id)` — the
per-org uniqueness intent SEC-04's constraint always meant, now correct
under SEC-05's multi-org schema. SEC-04's own race guarantee (two
concurrent inserts for the same org+document leave exactly one row) is
unaffected: the constraint still exists, just scoped one column wider.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-27
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_OLD_NAME = "uq_filings_source_document_id"


def upgrade() -> None:
    op.execute(f"ALTER TABLE filings DROP CONSTRAINT {_OLD_NAME}")
    op.execute(
        f"ALTER TABLE filings ADD CONSTRAINT {_OLD_NAME} "
        "UNIQUE (organization_id, source, source_document_id)"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE filings DROP CONSTRAINT {_OLD_NAME}")
    op.execute(f"ALTER TABLE filings ADD CONSTRAINT {_OLD_NAME} UNIQUE (source, source_document_id)")
