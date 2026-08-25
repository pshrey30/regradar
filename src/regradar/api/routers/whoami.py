"""Throwaway route exercising get_current_key + enforce_rate_limit over real HTTP.

No real authenticated route exists yet (API-04 etc. aren't built), so this
is what API-02's and API-03's own acceptance criteria (missing/malformed/
unknown/revoked/valid key, plus rate limiting, all via HTTP) actually run
against. Delete this file, and its mount point in api/main.py, once a real
authenticated route takes over that job.
"""

from fastapi import APIRouter, Depends

from regradar.api.deps import AuthenticatedKey
from regradar.api.middleware.rate_limit import enforce_rate_limit

router = APIRouter()


@router.get("/v1/_whoami")
async def whoami(key: AuthenticatedKey = Depends(enforce_rate_limit)) -> dict[str, str]:
    return {"role": key.role.value, "owner_label": key.owner_label}
