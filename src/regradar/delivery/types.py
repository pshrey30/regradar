"""Shared result type every delivery-channel client returns."""

from pydantic import BaseModel

from regradar.models.enums import DeliveryStatus


class DeliveryResult(BaseModel):
    status: DeliveryStatus
    response_code: int | None = None
    # A short, human-readable reason for a FAILED result — surfaced on the
    # Activity feed so a user can see why without going to logs. None for
    # SENT (nothing to explain) and for older rows written before this
    # field existed.
    error_message: str | None = None
