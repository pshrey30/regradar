"""ORM model for `webhooks` — registered by API consumers, independent of any single filing."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from regradar.core.db import Base
from regradar.models.enums import RiskLevel

if TYPE_CHECKING:
    from regradar.models.api_key import ApiKey
    from regradar.models.delivery import Delivery


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    hmac_secret: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    filter_domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    filter_min_risk: Mapped[RiskLevel | None] = mapped_column(
        SAEnum(RiskLevel, name="risk_level"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    api_key: Mapped["ApiKey"] = relationship(back_populates="webhooks")
    deliveries: Mapped[list["Delivery"]] = relationship(back_populates="webhook")
