"""ORG-11 — per-role alert destinations (Slack channel / email), additive
to the existing org-wide organization_delivery_settings.

Reuses the existing `api_key_role` Postgres enum type (defined by the
api_keys table's migration) rather than creating a new one — role names
must stay a single source of truth. Same service-only RLS precedent as
organization_delivery_settings (0012): no admin-facing API yet.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-16
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

_IS_SERVICE = "current_setting('app.current_role', true) = 'service'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE organization_role_delivery_settings (
            organization_id UUID NOT NULL REFERENCES organizations(id),
            role api_key_role NOT NULL,
            slack_webhook_url TEXT,
            email TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (organization_id, role)
        )
        """
    )
    op.execute("ALTER TABLE organization_role_delivery_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organization_role_delivery_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY organization_role_delivery_settings_service "
        "ON organization_role_delivery_settings "
        f"FOR ALL USING ({_IS_SERVICE}) WITH CHECK ({_IS_SERVICE})"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON organization_role_delivery_settings TO regradar_app"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY organization_role_delivery_settings_service "
        "ON organization_role_delivery_settings"
    )
    op.execute("DROP TABLE organization_role_delivery_settings")
