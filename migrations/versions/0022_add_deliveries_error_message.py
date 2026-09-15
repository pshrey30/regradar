"""Add error_message to deliveries.

A FAILED delivery row previously carried only a response_code (or nothing
at all, for a network-level failure) — no human-readable reason. The
Activity feed's failure tooltip needs an actual message, not just a
number, so DeliveryResult and every delivery client (webhook_dispatcher,
sendgrid_client, slack_client) and delivery_agent's own exception-catch
sites now populate this on every FAILED result.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deliveries", sa.Column("error_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("deliveries", "error_message")
