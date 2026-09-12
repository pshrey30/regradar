"""Activity feed — let any authenticated role SELECT their org's deliveries.

Every delivery attempt (Slack/email/webhook) is already logged in
`deliveries` with a real timestamp, but no customer-facing role could
read any of it until now — 0009's `deliveries_service` policy is
`FOR ALL USING (service)`, and 0017 only opened a narrow SELECT for a
webhook's own owner. A general "what alerts went out, and when" activity
view needs broader — but still org-scoped — read access.

This is additive, not a replacement: Postgres ORs multiple permissive
policies for the same command, so `deliveries_service`'s own access is
untouched, and 0017's webhook-owner policy still exists too (now
redundant for the webhook-owner case specifically, but harmless to
leave — removing it isn't necessary for this to work correctly).

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-12
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_AUTHENTICATED = (
    "current_setting('app.current_role', true) "
    "IN ('admin', 'analyst', 'executive', 'legal_counsel', 'eng_lead', 'service')"
)
_ORG_MATCH = "organization_id::text = current_setting('app.current_organization_id', true)"


def upgrade() -> None:
    op.execute(
        "CREATE POLICY deliveries_select_authenticated ON deliveries FOR SELECT "
        f"USING ({_AUTHENTICATED} AND {_ORG_MATCH})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY deliveries_select_authenticated ON deliveries")
