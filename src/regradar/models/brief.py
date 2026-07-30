"""ORM model for `briefs` — the Summarization Agent's output; one-to-one with filings."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from regradar.core.db import Base

if TYPE_CHECKING:
    from regradar.models.filing import Filing


class Brief(Base):
    __tablename__ = "briefs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("filings.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    executive_brief: Mapped[str] = mapped_column(Text, nullable=False)
    cco_summary: Mapped[str] = mapped_column(Text, nullable=False)
    analyst_summary: Mapped[str] = mapped_column(Text, nullable=False)
    engineer_summary: Mapped[str] = mapped_column(Text, nullable=False)
    model_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    rouge_l_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    filing: Mapped["Filing"] = relationship(back_populates="brief")
