"""Request/response models for GET/PUT /v1/organizations/me/profile."""

from pydantic import BaseModel, Field


class OrganizationProfileResponse(BaseModel):
    industry: str | None
    business_description: str | None
    watchlist_entities: list[str]
    products: list[str]
    risk_priorities: list[str]
    is_complete: bool


class OrganizationProfileRequest(BaseModel):
    """All five fields are required — Field(min_length=1) rejects both a
    missing field and an empty string/list, matching the onboarding
    design's "all fields required" decision at the schema level, before
    any DB write."""

    industry: str = Field(min_length=1)
    business_description: str = Field(min_length=1)
    watchlist_entities: list[str] = Field(min_length=1)
    products: list[str] = Field(min_length=1)
    risk_priorities: list[str] = Field(min_length=1)
