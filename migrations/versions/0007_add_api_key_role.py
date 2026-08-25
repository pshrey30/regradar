"""Add role to api_keys.

API-02 needs a role to resolve permissions per the Security & Access
Document's role model (Admin/Analyst/Executive/Legal Counsel/Eng Lead),
but FOUND-02's original api_keys table never included one. The table is
empty in every environment so far, so this adds the column NOT NULL with
no backfill needed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

ROLE_VALUES = ["admin", "analyst", "executive", "legal_counsel", "eng_lead"]


def upgrade() -> None:
    values_sql = ", ".join(f"'{v}'" for v in ROLE_VALUES)
    op.execute(f"CREATE TYPE api_key_role AS ENUM ({values_sql})")
    op.add_column(
        "api_keys",
        sa.Column(
            "role",
            postgresql.ENUM(*ROLE_VALUES, name="api_key_role", create_type=False),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "role")
    op.execute("DROP TYPE api_key_role")
