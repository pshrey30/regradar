"""Request/response models for GET/PUT /v1/organizations/me/profile."""

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("industry", "business_description")
    @classmethod
    def _strip_and_require_nonblank_str(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("watchlist_entities", "products", "risk_priorities")
    @classmethod
    def _strip_and_require_nonblank_list(cls, value: list[str]) -> list[str]:
        # Field(min_length=1) only guards against an empty list — it
        # happily lets [""] or ["   "] through, which would then also
        # pass is_complete()'s truthiness check (a non-empty list is
        # truthy even when every element is blank). Strip each entry and
        # drop any that become empty, then re-check the list isn't empty.
        stripped = [item.strip() for item in value if item.strip()]
        if not stripped:
            raise ValueError("must contain at least one non-blank entry")
        return stripped
