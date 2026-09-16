"""ORG-11 — per-organization business profile for relevance scoring.

Separate table rather than columns on `organizations`, matching
organization_delivery_settings' (0012) precedent: keeps `Organization`
the minimal RLS scaffold its own docstring says it's meant to be. No
admin-facing API to manage this exists yet — matching 0012's own
documented "that's a natural future ticket, not this one."

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-16
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

_IS_SERVICE = "current_setting('app.current_role', true) = 'service'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE organization_profiles (
            organization_id UUID PRIMARY KEY REFERENCES organizations(id),
            industry TEXT,
            business_description TEXT,
            watchlist_entities TEXT[] NOT NULL DEFAULT '{}',
            products TEXT[] NOT NULL DEFAULT '{}',
            risk_priorities TEXT[] NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("ALTER TABLE organization_profiles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organization_profiles FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY organization_profiles_service ON organization_profiles "
        f"FOR ALL USING ({_IS_SERVICE}) WITH CHECK ({_IS_SERVICE})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON organization_profiles TO regradar_app")


def downgrade() -> None:
    op.execute("DROP POLICY organization_profiles_service ON organization_profiles")
    op.execute("DROP TABLE organization_profiles")
