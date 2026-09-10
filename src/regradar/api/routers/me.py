"""GET/PATCH /v1/me — session/role resolution and self-service profile
updates for the dashboard's own logged-in user.

`organization_id` returns the caller's real organization (SEC-05) — every
key belongs to exactly one organization now. This route works identically
for a direct API key today and, once FE-02's SSO session resolves to an
API key behind the scenes, for a session cookie too — it only ever reads
the resolved `AuthenticatedKey`, never the header itself.

PATCH only ever touches the caller's own row (`key.id`) — there is no
`{id}` in the path, deliberately, so this endpoint can never be used to
edit anyone else's profile. Role changes for *other* users are a distinct,
Admin-only concern handled by `PATCH /v1/api-keys/{id}` instead.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.deps import AuthenticatedKey
from regradar.api.middleware.rate_limit import enforce_rate_limit, get_authenticated_db
from regradar.models.api_key import ApiKey
from regradar.schemas.me import MeResponse, UpdateProfileRequest

router = APIRouter()


@router.get("/v1/me", response_model=MeResponse)
async def get_me(key: AuthenticatedKey = Depends(enforce_rate_limit)) -> MeResponse:
    return MeResponse(
        role=key.role,
        organization_id=str(key.organization_id),
        display_name=key.owner_label,
        email=key.email,
        has_password=key.has_password,
    )


@router.patch("/v1/me", response_model=MeResponse)
async def update_me(
    body: UpdateProfileRequest,
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> MeResponse:
    row = await db.get(ApiKey, key.id)
    assert row is not None  # the row that authenticated this request
    row.owner_label = body.display_name
    await db.commit()
    return MeResponse(
        role=row.role,
        organization_id=str(row.organization_id),
        display_name=row.owner_label,
        email=row.email,
        has_password=row.password_hash is not None,
    )
