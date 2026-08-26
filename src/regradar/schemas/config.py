"""Pydantic models for POST/GET /v1/config/sources."""

from datetime import datetime

from pydantic import BaseModel

from regradar.models.enums import FilingSource


class SourceConfigUpdateRequest(BaseModel):
    sources: list[str]
    domains: list[str] = []


class SourceConfigResponse(BaseModel):
    source: FilingSource
    domains: list[str]
    is_active: bool
    poll_interval_seconds: int
    last_polled_at: datetime | None
