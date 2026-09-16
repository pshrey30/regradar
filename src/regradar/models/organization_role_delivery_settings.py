"""ORM model for `organization_role_delivery_settings` (ORG-11) — per-role
Slack channel / email alert destinations, additive to the org-wide
`organization_delivery_settings` row. A filing's domain picks which
role(s) it routes to via `domain_scope.roles_for_domain`; this table
supplies where that role's alert actually gets sent.
"""

import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from regradar.core.db import Base
from regradar.models.enums import ApiKeyRole, pg_enum_values


class OrganizationRoleDeliverySettings(Base):
    __tablename__ = "organization_role_delivery_settings"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), primary_key=True
    )
    role: Mapped[ApiKeyRole] = mapped_column(
        SAEnum(ApiKeyRole, name="api_key_role", values_callable=pg_enum_values), primary_key=True
    )
    slack_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
