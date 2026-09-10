"""Pydantic request/response models for GET/PATCH /v1/me."""

from pydantic import BaseModel, Field

from regradar.models.enums import ApiKeyRole


class MeResponse(BaseModel):
    role: ApiKeyRole
    organization_id: str | None
    display_name: str
    email: str | None = None
    has_password: bool = False


class UpdateProfileRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
