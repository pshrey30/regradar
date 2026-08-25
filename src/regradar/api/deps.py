"""FastAPI dependencies shared across route handlers."""

import uuid
from datetime import UTC, datetime

from fastapi import Header
from pydantic import BaseModel
from sqlalchemy import select

from regradar.api.errors import ApiError
from regradar.core.api_keys import hash_api_key
from regradar.core.db import get_session_factory
from regradar.models.api_key import ApiKey
from regradar.models.enums import ApiKeyRole

_INVALID_KEY_ERROR = ApiError(
    status_code=401,
    code="invalid_api_key",
    message="Missing, malformed, unknown, or revoked API key.",
)


class AuthenticatedKey(BaseModel):
    id: uuid.UUID
    role: ApiKeyRole
    owner_label: str
    rate_limit_per_minute: int


async def get_current_key(authorization: str = Header(default="")) -> AuthenticatedKey:
    """Resolve the calling API key from the Authorization header.

    Raises ApiError(401) for a missing, malformed, unknown, or revoked key —
    deliberately the same error in every case, so a caller can't use the
    response to distinguish "this key doesn't exist" from "this key was
    revoked".
    """
    if not authorization.startswith("Bearer "):
        raise _INVALID_KEY_ERROR

    presented_key = authorization.removeprefix("Bearer ").strip()
    if not presented_key:
        raise _INVALID_KEY_ERROR

    key_hash = hash_api_key(presented_key)

    session_factory = get_session_factory()
    async with session_factory() as db:
        result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        row = result.scalar_one_or_none()

        if row is None or not row.is_active:
            raise _INVALID_KEY_ERROR

        row.last_used_at = datetime.now(UTC)
        await db.commit()

        return AuthenticatedKey(
            id=row.id,
            role=row.role,
            owner_label=row.owner_label,
            rate_limit_per_minute=row.rate_limit_per_minute,
        )
