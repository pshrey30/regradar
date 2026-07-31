"""ORM model for `source_configs` — controls what the Ingestion Agent watches; standalone."""

import uuid
from datetime import datetime

from sqlalchemy import ARRAY, Boolean, Integer, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from regradar.core.db import Base
from regradar.models.enums import FilingSource, pg_enum_values


class SourceConfig(Base):
    __tablename__ = "source_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[FilingSource] = mapped_column(
        SAEnum(FilingSource, name="filing_source", values_callable=pg_enum_values), nullable=False
    )
    domains: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    feed_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    last_polled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
