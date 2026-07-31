"""ORM model for `eval_runs` — periodic snapshots of system-wide quality metrics; standalone."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, Integer, Numeric, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from regradar.core.db import Base
from regradar.models.enums import EvalRunType, pg_enum_values


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_type: Mapped[EvalRunType] = mapped_column(
        SAEnum(EvalRunType, name="eval_run_type", values_callable=pg_enum_values), nullable=False
    )
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    git_commit_sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    ragas_faithfulness: Mapped[float | None] = mapped_column(Float, nullable=True)
    ragas_context_recall: Mapped[float | None] = mapped_column(Float, nullable=True)
    rouge_l: Mapped[float | None] = mapped_column(Float, nullable=True)
    alert_precision: Mapped[float | None] = mapped_column(Float, nullable=True)
    alert_recall: Mapped[float | None] = mapped_column(Float, nullable=True)
    hallucination_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_f1: Mapped[float | None] = mapped_column(Float, nullable=True)
    p99_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_cost_per_filing_usd: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
