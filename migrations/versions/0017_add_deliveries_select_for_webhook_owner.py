"""FE-06 — let a webhook's owner (or admin) SELECT its own deliveries rows.

SEC-01's original `deliveries_service` policy is `FOR ALL USING (service)`,
meaning no non-service caller could ever SELECT a deliveries row — correct
for the tickets built so far (nothing needed it), but FE-06's webhook list
screen needs exactly this: "delivery success/failure indicators per
webhook, sourced from recent deliveries rows." A blanket authenticated-SELECT
policy (the `filings`/`briefs` pattern) would over-expose Slack/email
recipient addresses and full delivery history across the whole
organization; this policy is scoped tighter than that instead — a caller
sees a deliveries row only when it's tied to a `webhook_id` they
themselves own (or they're admin/service). `channel != 'webhook'` rows have
`webhook_id IS NULL`, which never matches the subquery, so this grants
nothing beyond what FE-06 actually needs.

Additive, not a replacement: Postgres OR's multiple permissive policies
for the same command together, so `deliveries_service`'s own SELECT access
for the service role is untouched by this.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-10
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_IS_ADMIN = "current_setting('app.current_role', true) = 'admin'"
_IS_SERVICE = "current_setting('app.current_role', true) = 'service'"
_OWNS_WEBHOOK = (
    "webhook_id IN ("
    "SELECT id FROM webhooks "
    "WHERE api_key_id::text = current_setting('app.current_api_key_id', true)"
    ")"
)


def upgrade() -> None:
    op.execute(
        "CREATE POLICY deliveries_select_own_webhook ON deliveries FOR SELECT "
        f"USING ({_IS_ADMIN} OR {_IS_SERVICE} OR {_OWNS_WEBHOOK})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY deliveries_select_own_webhook ON deliveries")
