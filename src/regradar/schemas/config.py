"""Pydantic models for POST/GET /v1/config/sources."""

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

from regradar.models.enums import FilingSource


class SourceConfigUpdateRequest(BaseModel):
    sources: list[str]
    domains: list[str] = []
    # Only FDA's connector reads a configured feed_url today (SEC/FINRA are
    # driven entirely by their own APIs, no URL to pick) — kept as its own
    # named field rather than a generic per-source map, since every other
    # source has nothing to put there yet.
    fda_feed_url: str | None = None

    @field_validator("fda_feed_url")
    @classmethod
    def _validate_fda_feed_url(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("fda_feed_url must be a full http(s) URL.")
        return value


class SourceConfigResponse(BaseModel):
    source: FilingSource
    domains: list[str]
    is_active: bool
    poll_interval_seconds: int
    last_polled_at: datetime | None
    feed_url: str | None = None


class PollSourceResponse(BaseModel):
    source: FilingSource
    new_filing_count: int
    last_polled_at: datetime | None
    # Every filing still at status=ingested for this org after the poll —
    # not just ones this exact call fetched — is handed off to the pipeline
    # in the background (see fetch_now's docstring for why "everything
    # pending", not just "this call's new ones"). The frontend opens a
    # status/ws connection per id to show each one's progress live.
    processing_filing_ids: list[uuid.UUID]


class FetchNowRequest(BaseModel):
    sources: list[str]


class FetchNowResult(BaseModel):
    source: FilingSource
    new_filing_count: int


class FetchNowResponse(BaseModel):
    results: list[FetchNowResult]
    processing_filing_ids: list[uuid.UUID]
