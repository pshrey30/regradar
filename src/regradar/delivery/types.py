"""Shared result type every delivery-channel client returns."""

from pydantic import BaseModel

from regradar.models.enums import DeliveryStatus


class DeliveryResult(BaseModel):
    status: DeliveryStatus
    response_code: int | None = None
