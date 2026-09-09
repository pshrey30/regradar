"""FE-04 — pg_notify trigger on filings.status changes.

FE-04's ticket calls for live-updating filing status via Supabase
Realtime, but this deployment's Postgres is local Docker, not a real
Supabase-hosted project (SUPABASE_URL/SERVICE_ROLE_KEY in .env are
placeholder-only) — Realtime specifically isn't available here. Built the
equivalent behavior on infrastructure that's actually real instead: a
trigger NOTIFYs on the 'filing_status_changed' channel whenever a filing's
status actually changes, and the API's WebSocket endpoint (see
filings.py's status_stream) LISTENs on that channel and forwards matching
messages to subscribed browser clients.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-09
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION notify_filing_status_change() RETURNS trigger AS $$
        BEGIN
            PERFORM pg_notify(
                'filing_status_changed',
                json_build_object(
                    'filing_id', NEW.id,
                    'organization_id', NEW.organization_id,
                    'status', NEW.status
                )::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER filings_notify_status_change
        AFTER UPDATE OF status ON filings
        FOR EACH ROW
        WHEN (OLD.status IS DISTINCT FROM NEW.status)
        EXECUTE FUNCTION notify_filing_status_change();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS filings_notify_status_change ON filings")
    op.execute("DROP FUNCTION IF EXISTS notify_filing_status_change()")
