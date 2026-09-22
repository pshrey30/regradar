"""ORM model for `organization_profiles` (ORG-11) — per-organization business
context (industry, watchlist entities, products, risk priorities) the
Relevance Agent uses to score how much a filing matters to *this*
organization's business, not just how objectively severe it is.

Separate table rather than columns on `organizations` itself, matching
organization_delivery_settings' precedent: keeps `Organization` the
minimal RLS scaffold its own docstring says it's meant to be.
"""

import uuid
from datetime import datetime

from sqlalchemy import ARRAY, ForeignKey, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from regradar.core.db import Base


class OrganizationProfile(Base):
    __tablename__ = "organization_profiles"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), primary_key=True
    )
    industry: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    watchlist_entities: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    products: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    risk_priorities: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


def is_complete(profile: "OrganizationProfile | None") -> bool:
    """Whether every field this project requires at onboarding is
    actually populated — the single predicate the onboarding-redirect
    check (API-11), the pipeline gate (API-11), and /v1/me (API-11) all
    share, so they can't silently drift apart on what "complete" means."""
    if profile is None:
        return False
    return bool(
        profile.industry
        and profile.business_description
        and profile.watchlist_entities
        and profile.products
        and profile.risk_priorities
    )
