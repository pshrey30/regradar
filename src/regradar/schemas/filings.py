"""Pydantic response models for GET /v1/filings."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from regradar.models.enums import FilingDomain, RiskLevel


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
