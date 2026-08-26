"""ORM model for `extractions` — the Analysis Agent's structured output; one-to-one with filings."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from regradar.core.db import Base

if TYPE_CHECKING:
    from regradar.models.filing import Filing


class Extraction(Base):
    __tablename__ = "extractions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("filings.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    obligations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    deadlines: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    risk_flags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    affected_products: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    key_entities: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    competitor_mentions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    model_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_model_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    similar_filing_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    filing: Mapped["Filing"] = relationship(back_populates="extraction")
