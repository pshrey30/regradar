"""ORM model for `api_keys` — authenticates API consumers."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from regradar.core.db import Base
from regradar.models.enums import ApiKeyRole, pg_enum_values

if TYPE_CHECKING:
    from regradar.models.webhook import Webhook


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    owner_label: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[ApiKeyRole] = mapped_column(
        SAEnum(ApiKeyRole, name="api_key_role", values_callable=pg_enum_values),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    webhooks: Mapped[list["Webhook"]] = relationship(back_populates="api_key")
