"""Pydantic request/response models for /v1/api-keys."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from regradar.models.enums import ApiKeyRole


class ApiKeyCreateRequest(BaseModel):
    owner_label: str = Field(min_length=1)
    role: ApiKeyRole
    rate_limit_per_minute: int | None = None


class ApiKeyResponse(BaseModel):
    """Never carries the plaintext key or key_hash — used for every
    response except the one right after creation."""

    id: uuid.UUID
    owner_label: str
    role: ApiKeyRole
    is_active: bool
    rate_limit_per_minute: int
    # Last 4 characters only — None for a key minted before this column
    # existed (see migration 0018).
    key_suffix: str | None
    created_at: datetime
    last_used_at: datetime | None


class ApiKeyCreateResponse(ApiKeyResponse):
    """The one and only response that includes the plaintext key — shown
    exactly once, at creation."""

    key: str
