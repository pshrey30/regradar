"""ORM model for `filings` — one row per ingested regulatory document."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from regradar.core.db import Base
from regradar.models.enums import (
    FilingDomain,
    FilingSource,
    FilingStatus,
    RiskLevel,
    pg_enum_values,
)

if TYPE_CHECKING:
    from regradar.models.brief import Brief
    from regradar.models.chunk import FilingChunk
    from regradar.models.delivery import Delivery
    from regradar.models.extraction import Extraction


class Filing(Base):
    __tablename__ = "filings"
    __table_args__ = (
        UniqueConstraint("source", "source_document_id", name="uq_filings_source_document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[FilingSource] = mapped_column(
        SAEnum(FilingSource, name="filing_source", values_callable=pg_enum_values), nullable=False
    )
    source_document_id: Mapped[str] = mapped_column(Text, nullable=False)
    entity_name: Mapped[str] = mapped_column(Text, nullable=False)
    filing_type: Mapped[str] = mapped_column(Text, nullable=False)
    filing_url: Mapped[str] = mapped_column(Text, nullable=False)
    raw_pdf_s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    status: Mapped[FilingStatus] = mapped_column(
        SAEnum(FilingStatus, name="filing_status", values_callable=pg_enum_values),
        nullable=False,
        default=FilingStatus.INGESTED,
    )
    domain: Mapped[FilingDomain | None] = mapped_column(
        SAEnum(FilingDomain, name="filing_domain", values_callable=pg_enum_values), nullable=True
    )
    risk_level: Mapped[RiskLevel | None] = mapped_column(
        SAEnum(RiskLevel, name="risk_level", values_callable=pg_enum_values), nullable=True
    )
    priority_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    chunks: Mapped[list["FilingChunk"]] = relationship(
        back_populates="filing", cascade="all, delete-orphan"
    )
    extraction: Mapped["Extraction | None"] = relationship(
        back_populates="filing", uselist=False, cascade="all, delete-orphan"
    )
    brief: Mapped["Brief | None"] = relationship(
        back_populates="filing", uselist=False, cascade="all, delete-orphan"
    )
    deliveries: Mapped[list["Delivery"]] = relationship(
        back_populates="filing", cascade="all, delete-orphan"
    )
