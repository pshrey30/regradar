"""GET /v1/me — session/role resolution for the future dashboard's app-load call.

`organization_id` returns the caller's real organization (SEC-05) — every
key belongs to exactly one organization now. This route works identically
for a direct API key today and, once FE-02's SSO session resolves to an
API key behind the scenes, for a session cookie too — it only ever reads
the resolved `AuthenticatedKey`, never the header itself.
"""

from fastapi import APIRouter, Depends

from regradar.api.deps import AuthenticatedKey
from regradar.api.middleware.rate_limit import enforce_rate_limit
from regradar.schemas.me import MeResponse

router = APIRouter()


@router.get("/v1/me", response_model=MeResponse)
async def get_me(key: AuthenticatedKey = Depends(enforce_rate_limit)) -> MeResponse:
    return MeResponse(role=key.role, organization_id=str(key.organization_id), display_name=key.owner_label)
