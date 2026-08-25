"""Throwaway route exercising get_current_key over real HTTP.

No real authenticated route exists yet (API-04 etc. aren't built), so this
is what API-02's own acceptance criteria (missing/malformed/unknown/revoked/
valid key, all via HTTP) actually run against. Delete this file, and its
mount point in api/main.py, once a real authenticated route takes over that
job.
"""

from fastapi import APIRouter, Depends

from regradar.api.deps import AuthenticatedKey, get_current_key

router = APIRouter()


@router.get("/v1/_whoami")
async def whoami(key: AuthenticatedKey = Depends(get_current_key)) -> dict[str, str]:
    return {"role": key.role.value, "owner_label": key.owner_label}
