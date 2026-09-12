"""Pydantic response model for GET /v1/activity."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from regradar.models.enums import DeliveryChannel, DeliveryStatus, FilingDomain, RiskLevel


class ActivityItem(BaseModel):
    id: uuid.UUID
    filing_id: uuid.UUID
    entity_name: str
    filing_type: str
    domain: FilingDomain | None
    risk_level: RiskLevel | None
    channel: DeliveryChannel
    status: DeliveryStatus
    is_fallback: bool
    # The moment this alert actually went out — falls back to when the
    # attempt was created for a delivery that hasn't (or never will)
    # send, e.g. one still pending or one that failed before ever
    # reaching sent_at.
    at: datetime
