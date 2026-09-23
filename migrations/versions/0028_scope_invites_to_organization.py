"""API-11 fix — scope invites to organization_id.

Final whole-branch review of API-11 (self-serve Admin signup) found a real
cross-tenant leak: `invites` had no `organization_id` at all — every
invited signup landed in whichever org was created first (`signup()` and
`_find_or_create_api_key` in api/routers/auth.py both did
`select(Organization.id).order_by(Organization.created_at.asc()).limit(1)`),
and 0020's single `invites_admin_or_service FOR ALL` policy granted ANY
Admin unrestricted access to ALL invites regardless of org. That was
harmless back when there was truly only one organization in the whole
system — it stopped being harmless the moment `POST /v1/auth/signup-org`
(this same branch) let anyone create a brand-new second org and become
its Admin: that new Admin could then list/see/revoke every OTHER org's
invites, and any teammate they invited into their own org would actually
land in the ORIGINAL first-created org instead.

Adds `organization_id` to `invites` using the exact same
add-column/backfill/set-not-null/add-FK sequence 0010 used for its five
org-scoped tables, backfilling every pre-existing invite to the SAME
`_DEFAULT_ORG_ID` 0010 backfilled everything else to — anything else
would orphan those rows under RLS relative to every other org-scoped
table.

Replaces 0020's single ALL/admin-or-service policy with 4 op-specific
policies mirroring 0010's `source_configs` shape (not 0027's
`organization_profiles` 3-policy shape) since, unlike a profile, invites
are actively created/listed/managed by Admins across all 4 operations:
SELECT/INSERT/UPDATE/DELETE each require (admin AND own org) OR service.

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-22
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"

_ADMIN_OR_SERVICE = "current_setting('app.current_role', true) IN ('admin', 'service')"
_IS_SERVICE = "current_setting('app.current_role', true) = 'service'"
_IS_ADMIN = "current_setting('app.current_role', true) = 'admin'"
_ORG_MATCH = "organization_id::text = current_setting('app.current_organization_id', true)"

_WRITE_CHECK = f"(({_IS_ADMIN} AND {_ORG_MATCH}) OR {_IS_SERVICE})"


def upgrade() -> None:
    op.execute("ALTER TABLE invites ADD COLUMN organization_id UUID")
    op.execute(
        f"UPDATE invites SET organization_id = '{_DEFAULT_ORG_ID}' WHERE organization_id IS NULL"
    )
    op.execute("ALTER TABLE invites ALTER COLUMN organization_id SET NOT NULL")
    op.execute(
        "ALTER TABLE invites ADD CONSTRAINT fk_invites_organization_id "
        "FOREIGN KEY (organization_id) REFERENCES organizations(id)"
    )

    op.execute("DROP POLICY invites_admin_or_service ON invites")
    op.execute(
        f"CREATE POLICY invites_select ON invites FOR SELECT USING ({_WRITE_CHECK})"
    )
    op.execute(
        f"CREATE POLICY invites_insert ON invites FOR INSERT WITH CHECK ({_WRITE_CHECK})"
    )
    op.execute(
        f"CREATE POLICY invites_update ON invites FOR UPDATE USING ({_WRITE_CHECK})"
    )
    op.execute(
        f"CREATE POLICY invites_delete ON invites FOR DELETE USING ({_WRITE_CHECK})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY invites_select ON invites")
    op.execute("DROP POLICY invites_insert ON invites")
    op.execute("DROP POLICY invites_update ON invites")
    op.execute("DROP POLICY invites_delete ON invites")
    op.execute(
        f"CREATE POLICY invites_admin_or_service ON invites FOR ALL "
        f"USING ({_ADMIN_OR_SERVICE}) WITH CHECK ({_ADMIN_OR_SERVICE})"
    )

    op.execute("ALTER TABLE invites DROP CONSTRAINT fk_invites_organization_id")
    op.execute("ALTER TABLE invites DROP COLUMN organization_id")
