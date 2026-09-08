"""DELIV-01 — per-organization Slack webhook URL storage.

DELIV-01's acceptance criteria explicitly requires the Slack webhook URL
be "stored per-organization (via source_configs or an org settings
table), not hardcoded." AGENT-10's original delivery_agent.py predates
SEC-05's `organizations` table and used a single global
settings.slack_webhook_url for every org — a deliberate, documented
deviation at the time since no organization concept existed yet. Now
that it does, this closes that gap.

New `organization_delivery_settings` table, not a column on
`organizations` itself: a Slack Incoming Webhook URL is a bearer
credential, but `organizations` is broadly SELECT-able by any
authenticated role (0010's `organizations_select` policy), and Postgres
RLS restricts rows, not columns — a credential column there would leak
to every authenticated role via direct DB access regardless of what any
API route exposes. This table is service-only instead (no admin-facing
API to manage it exists yet, matching 0010's own "no organization-
management surface" precedent — that's a natural future ticket, not
this one).

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-08
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_IS_SERVICE = "current_setting('app.current_role', true) = 'service'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE organization_delivery_settings (
            organization_id UUID PRIMARY KEY REFERENCES organizations(id),
            slack_webhook_url TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("ALTER TABLE organization_delivery_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organization_delivery_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY organization_delivery_settings_service ON organization_delivery_settings "
        f"FOR ALL USING ({_IS_SERVICE}) WITH CHECK ({_IS_SERVICE})"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON organization_delivery_settings TO regradar_app"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY organization_delivery_settings_service ON organization_delivery_settings"
    )
    op.execute("DROP TABLE organization_delivery_settings")
