"""Add feed_url to source_configs.

The TAD describes the RSS/feed URL for a source as "a config value, per
source_configs in the database schema," but the originally-documented
source_configs table never actually included a column for it. ING-02
needs this to satisfy its own acceptance criterion ("Feed URL is read
from source_configs, not hardcoded").

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-31
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_configs", sa.Column("feed_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("source_configs", "feed_url")
