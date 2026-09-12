"""ORM model for `invites` — Admin-issued, single-use signup gates.

An invite is purely a gate on whether someone can create an account at
all — it does not carry a role. The person signing up chooses their own
role, from a restricted set that excludes Admin (see schemas/auth.py's
SELF_SELECTABLE_ROLES).
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from regradar.core.db import Base


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    code_suffix: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    used_by_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
