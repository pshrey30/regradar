"""Pydantic response models for GET /v1/filings and GET /v1/filings/{id}."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from regradar.models.enums import FilingDomain, FilingStatus, RiskLevel


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
    top_k: int = 5


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
