"""Pydantic request/response models for /v1/webhooks."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from regradar.models.enums import RiskLevel


class WebhookCreateRequest(BaseModel):
    url: str
    filter_domain: str | None = None
    filter_min_risk: RiskLevel | None = None


class WebhookResponse(BaseModel):
    """Never carries hmac_secret — used for every response except the one
    right after creation."""

    id: uuid.UUID
    url: str
    is_active: bool
    filter_domain: str | None
    filter_min_risk: RiskLevel | None
    created_at: datetime


class WebhookCreateResponse(WebhookResponse):
    """The one and only response that includes the plaintext hmac_secret —
    shown exactly once, at creation."""

    hmac_secret: str
