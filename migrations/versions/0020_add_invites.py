"""Invite-gated signup — new `invites` table + RLS.

Signup is no longer open: a new account requires a valid, unused invite
code. The invite is purely a gate on "can this person create an account
at all" — it does NOT carry a role. The role is chosen by the person
signing up, from a restricted set that excludes Admin (see
schemas/auth.py's SELF_SELECTABLE_ROLES and SignupRequest's validator).
An Admin account is only ever created by another Admin directly, never
through this self-service signup path — self-service role escalation
stays impossible either way.

Codes are hashed the same way API keys already are (see core/api_keys.py's
docstring for why a fast, unsalted SHA-256 digest is the right choice for
a high-entropy random token, unlike a low-entropy user password) — the
plaintext code is shown to the Admin exactly once, at creation, and is
never recoverable afterward, matching the show-once pattern this project
already uses for webhook HMAC secrets and API keys.

`used_at IS NULL` is what makes a code single-use; the consuming UPDATE
in the signup route is a conditional `WHERE used_at IS NULL` write (the
same race-safe pattern SEC-04 already established for duplicate-filing
protection) so two concurrent signups with the same code can never both
succeed.

Admin/service only — this table doesn't need per-user scoping since
nobody except an Admin ever manages invites, and the one read/write a
signing-up (not-yet-authenticated) request needs happens under the
`service` role, same as the rest of signup() already does.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_ADMIN_OR_SERVICE = "current_setting('app.current_role', true) IN ('admin', 'service')"


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code_hash", sa.Text, nullable=False, unique=True),
        # Last 4 characters only, captured at creation, purely for display
        # in an admin list — never used for authentication, same
        # nothing-sensitive-recoverable reasoning as api_keys.key_suffix.
        sa.Column("code_suffix", sa.Text, nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("used_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("used_by_email", sa.Text, nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    # The restricted, non-superuser 'regradar_app' role every real
    # connection uses (see 0009's own docstring) needs an explicit GRANT
    # on any new table — RLS policies only restrict rows *within* a table
    # the role can already touch at the Postgres privilege level; without
    # this, every query against invites would fail with a permission
    # error regardless of the policy below.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON invites TO regradar_app")
    op.execute("ALTER TABLE invites ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invites FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY invites_admin_or_service ON invites FOR ALL "
        f"USING ({_ADMIN_OR_SERVICE}) WITH CHECK ({_ADMIN_OR_SERVICE})"
    )


def downgrade() -> None:
    op.drop_table("invites")
