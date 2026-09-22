"""API-11 — self-serve Admin signup & organization onboarding.

Adds FilingStatus.NEEDS_ORGANIZATION_SETUP (0026's filing_status enum gets
a new value the same way 0023 added `engineering` to filing_domain).

Also fixes organization_profiles' RLS policy (0024): it was created
service-role-only ("no admin-facing API to manage this exists yet" — 0024's
own docstring), but this migration is exactly that API. Replaces the
single ALL/service policy with the same shape 0009/0010 already established
for source_configs: SELECT for any authenticated role scoped to their own
org, INSERT/UPDATE for Admin (scoped to their own org) or service. No
DELETE policy — nothing in this app ever deletes a profile.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-22
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

_NEW_STATUS = "needs_organization_setup"
_ORIGINAL_STATUSES = [
    "ingested",
    "classifying",
    "needs_classification",
    "needs_review",
    "retrieving",
    "analyzing",
    "summarizing",
    "delivering",
    "complete",
    "failed",
]

_IS_SERVICE = "current_setting('app.current_role', true) = 'service'"
_IS_ADMIN = "current_setting('app.current_role', true) = 'admin'"
_AUTHENTICATED = (
    "current_setting('app.current_role', true) IN "
    "('admin', 'analyst', 'executive', 'legal_counsel', 'eng_lead')"
)
_ORG_MATCH = "organization_id::text = current_setting('app.current_organization_id', true)"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE filing_status ADD VALUE '{_NEW_STATUS}'")

    op.execute("DROP POLICY organization_profiles_service ON organization_profiles")
    op.execute(
        f"CREATE POLICY organization_profiles_select ON organization_profiles FOR SELECT "
        f"USING ({_AUTHENTICATED} AND {_ORG_MATCH})"
    )
    write_check = f"(({_IS_ADMIN} AND {_ORG_MATCH}) OR {_IS_SERVICE})"
    op.execute(
        f"CREATE POLICY organization_profiles_insert ON organization_profiles FOR INSERT "
        f"WITH CHECK {write_check}"
    )
    op.execute(
        f"CREATE POLICY organization_profiles_update ON organization_profiles FOR UPDATE "
        f"USING {write_check}"
    )


def downgrade() -> None:
    op.execute("DROP POLICY organization_profiles_select ON organization_profiles")
    op.execute("DROP POLICY organization_profiles_insert ON organization_profiles")
    op.execute("DROP POLICY organization_profiles_update ON organization_profiles")
    op.execute(
        f"CREATE POLICY organization_profiles_service ON organization_profiles "
        f"FOR ALL USING ({_IS_SERVICE}) WITH CHECK ({_IS_SERVICE})"
    )

    op.execute(
        f"UPDATE filings SET status = 'needs_review' WHERE status = '{_NEW_STATUS}'"
    )
    values_sql = ", ".join(f"'{v}'" for v in _ORIGINAL_STATUSES)
    op.execute(f"CREATE TYPE filing_status_old AS ENUM ({values_sql})")
    op.execute(
        "ALTER TABLE filings ALTER COLUMN status TYPE filing_status_old "
        "USING status::text::filing_status_old"
    )
    op.execute("DROP TYPE filing_status")
    op.execute("ALTER TYPE filing_status_old RENAME TO filing_status")
