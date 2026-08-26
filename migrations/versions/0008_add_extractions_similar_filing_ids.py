"""Add similar_filing_ids to extractions.

API-05's GET /v1/filings/{id} needs a similar_filings field, but nothing
persists AGENT-06's retrieval results anywhere — PipelineState.retrieved_chunks
only ever lives transiently in-memory during a pipeline run. This column
closes that gap: workers/pipeline_tasks.py now writes the distinct filing_ids
from the retrieval step's chunks here alongside the rest of the Extraction
row, so API-05 has something real to read.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extractions",
        sa.Column("similar_filing_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extractions", "similar_filing_ids")
