"""ORM model for `organization_delivery_settings` (DELIV-01) — per-organization
delivery-channel configuration.

Split from `organizations` itself rather than a column on that table: a
Slack Incoming Webhook URL is a bearer credential (posting to it lets
anyone send messages to that Slack channel), but `organizations` is
broadly SELECT-able by any authenticated role (SEC-05's
`organizations_select` policy) since a caller needs to resolve its own
org's name. Postgres RLS restricts rows, not columns, so a credential
column on that table would leak to every authenticated role via direct
DB access even though no route exposes it today. This table is
service-only instead, matching how `webhooks.hmac_secret` and
`deliveries` are each kept behind a tighter boundary than a table's
general-purpose read policy.
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from regradar.core.db import Base


class OrganizationDeliverySettings(Base):
    __tablename__ = "organization_delivery_settings"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), primary_key=True
    )
    slack_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
