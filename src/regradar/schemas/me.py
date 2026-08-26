"""Pydantic response model for GET /v1/me."""

from pydantic import BaseModel

from regradar.models.enums import ApiKeyRole


class MeResponse(BaseModel):
    role: ApiKeyRole
    organization_id: str | None
    display_name: str
