"""Pydantic response models for GET /v1/filings and GET /v1/filings/{id}."""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from regradar.models.enums import DeliveryStatus, FilingDomain, FilingStatus, RiskLevel

# Same pattern as schemas/auth.py's _normalize_and_validate_email — kept
# as its own small copy rather than importing a signup-specific private
# helper from an unrelated module for what's really just "is this
# syntactically an email address," not an auth concern.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class FilingListItem(BaseModel):
    id: uuid.UUID
    entity_name: str
    filing_type: str
    domain: FilingDomain | None
    risk_level: RiskLevel | None
    published_at: datetime
    executive_brief: str


class FilingListResponse(BaseModel):
    data: list[FilingListItem]
    page: int
    page_size: int
    total: int


class PendingFilingItem(BaseModel):
    """A filing that hasn't reached `complete` yet — no Brief row exists,
    so this deliberately carries none of FilingListItem's brief-derived
    fields."""

    id: uuid.UUID
    entity_name: str
    filing_type: str
    source: str
    status: FilingStatus
    ingested_at: datetime
    processing_error: str | None


class PendingFilingsResponse(BaseModel):
    data: list[PendingFilingItem]
    page: int
    page_size: int
    total: int


class ProcessFilingResponse(BaseModel):
    id: uuid.UUID
    status: FilingStatus


class SimilarFiling(BaseModel):
    id: uuid.UUID
    entity_name: str
    filing_type: str
    published_at: datetime


class BriefSummary(BaseModel):
    executive_brief: str


class ExtractionDetail(BaseModel):
    obligations: list
    deadlines: list
    risk_flags: list
    affected_products: list
    key_entities: list
    competitor_mentions: list


class FilingDetailResponse(BaseModel):
    """Not used as a route's response_model — the Executive role must have

    the `extraction` key entirely absent from the JSON body, not merely
    null, which a fixed-schema response_model can't express. This class
    documents the full shape (used by the route to build a plain dict) and
    exists mainly so mypy can check field construction.
    """

    id: uuid.UUID
    entity_name: str
    filing_type: str
    domain: FilingDomain | None
    risk_level: RiskLevel | None
    priority_score: float | None
    published_at: datetime
    status: FilingStatus
    brief: BriefSummary | None
    similar_filings: list[SimilarFiling]


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


class SearchSource(BaseModel):
    filing_id: uuid.UUID
    excerpt: str
    entity_name: str


class SearchResponse(BaseModel):
    answer: str | None
    sources: list[SearchSource]
    degraded: bool = False


class PersonaBriefResponse(BaseModel):
    persona: str
    summary: str


class ManualAlertRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        if not _EMAIL_PATTERN.match(value):
            raise ValueError("Not a valid email address")
        return value.lower()


class ManualAlertResponse(BaseModel):
    status: DeliveryStatus
    recipient: str
    error_message: str | None = None
