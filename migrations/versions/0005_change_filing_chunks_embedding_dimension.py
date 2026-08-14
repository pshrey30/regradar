"""Change filing_chunks.embedding from vector(1536) to vector(768).

AGENT-05 uses a local Ollama embedding model (nomic-embed-text, 768
dimensions) instead of OpenAI's text-embedding-3-small (1536), per the
cost-conscious local-inference policy established in AGENT-03. The table
is currently empty — nothing has ever written to filing_chunks.embedding
— so this is a clean type change with no data to convert.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-14
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE filing_chunks ALTER COLUMN embedding TYPE vector(768) USING NULL::vector(768)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE filing_chunks ALTER COLUMN embedding TYPE vector(1536) USING NULL::vector(1536)"
    )
