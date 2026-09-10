"""POST/GET /v1/api-keys, DELETE /v1/api-keys/{id} — Admin-only API key
issuance and revocation, per FE-07 (previously CLI-only, see cli.py's
`_create_api_key` docstring).

Follows the same show-once pattern API-08 established for webhook HMAC
secrets: the plaintext key is returned in the creation response only,
never again. DELETE revokes (is_active = False) rather than hard-deleting
— an existing key_hash staying in place, just inert, matches how
`get_current_key` already treats `is_active = False` as invalid without
needing to distinguish "revoked" from "never existed" anywhere else.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.deps import AuthenticatedKey
from regradar.api.errors import ApiError
from regradar.api.middleware.rate_limit import enforce_rate_limit, get_authenticated_db
from regradar.core.api_keys import generate_api_key, hash_api_key
from regradar.models.api_key import ApiKey
from regradar.models.enums import ApiKeyRole
from regradar.schemas.api_keys import ApiKeyCreateRequest, ApiKeyCreateResponse, ApiKeyResponse

router = APIRouter()

_DEFAULT_RATE_LIMIT_PER_MINUTE = 60  # matches ApiKey.rate_limit_per_minute's DB default (FOUND-02)

_ADMIN_ONLY_ERROR = ApiError(
    status_code=403,
    code="forbidden",
    message="Only the Admin role can manage API keys.",
)


def _require_admin(key: AuthenticatedKey) -> None:
    if key.role != ApiKeyRole.ADMIN:
        raise _ADMIN_ONLY_ERROR


@router.post("/v1/api-keys", response_model=ApiKeyCreateResponse, status_code=201)
async def create_api_key(
    body: ApiKeyCreateRequest,
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> ApiKeyCreateResponse:
    _require_admin(key)

    plaintext_key = generate_api_key()
    rate_limit = body.rate_limit_per_minute or _DEFAULT_RATE_LIMIT_PER_MINUTE

    new_key = ApiKey(
        id=uuid.uuid4(),
        organization_id=key.organization_id,
        key_hash=hash_api_key(plaintext_key),
        key_suffix=plaintext_key[-4:],
        owner_label=body.owner_label,
        role=body.role,
        is_active=True,
        rate_limit_per_minute=rate_limit,
        created_at=datetime.now(UTC),
    )
    db.add(new_key)
    await db.commit()

    return ApiKeyCreateResponse(
        id=new_key.id,
        owner_label=new_key.owner_label,
        role=new_key.role,
        is_active=new_key.is_active,
        rate_limit_per_minute=new_key.rate_limit_per_minute,
        key_suffix=new_key.key_suffix,
        created_at=new_key.created_at,
        last_used_at=None,
        key=plaintext_key,
    )


@router.get("/v1/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> list[ApiKeyResponse]:
    _require_admin(key)

    stmt = select(ApiKey).where(ApiKey.organization_id == key.organization_id)
    rows = (await db.execute(stmt)).scalars().all()

    return [
        ApiKeyResponse(
            id=row.id,
            owner_label=row.owner_label,
            role=row.role,
            is_active=row.is_active,
            rate_limit_per_minute=row.rate_limit_per_minute,
            key_suffix=row.key_suffix,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
        )
        for row in rows
    ]


@router.delete("/v1/api-keys/{key_id}", response_model=ApiKeyResponse)
async def revoke_api_key(
    key_id: uuid.UUID,
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> ApiKeyResponse:
    _require_admin(key)

    target = await db.get(ApiKey, key_id)
    if target is None or target.organization_id != key.organization_id:
        raise ApiError(
            status_code=404, code="api_key_not_found", message="No API key exists with this ID."
        )

    target.is_active = False
    await db.commit()

    return ApiKeyResponse(
        id=target.id,
        owner_label=target.owner_label,
        role=target.role,
        is_active=target.is_active,
        rate_limit_per_minute=target.rate_limit_per_minute,
        key_suffix=target.key_suffix,
        created_at=target.created_at,
        last_used_at=target.last_used_at,
    )
