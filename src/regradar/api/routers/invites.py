"""POST/GET /v1/invites — Admin-only invite issuance and listing.

Invite-gated signup: `POST /v1/auth/signup` now requires a valid, unused
invite code. The invite is purely a gate — it carries no role; the person
signing up chooses their own role from a restricted, non-Admin set (see
schemas/auth.py's SELF_SELECTABLE_ROLES and auth.py's signup() for the
security reasoning).

Follows the same show-once pattern API-08/FE-07 established for webhook
HMAC secrets and API keys: the plaintext code is returned in the creation
response only, never again.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.deps import AuthenticatedKey
from regradar.api.errors import ApiError
from regradar.api.middleware.rate_limit import enforce_rate_limit, get_authenticated_db
from regradar.core.api_keys import hash_api_key
from regradar.core.invites import generate_invite_code
from regradar.models.enums import ApiKeyRole
from regradar.models.invite import Invite
from regradar.schemas.invites import InviteCreateResponse, InviteResponse

router = APIRouter()

_ADMIN_ONLY_ERROR = ApiError(
    status_code=403, code="forbidden", message="Only the Admin role can manage invites."
)


def _require_admin(key: AuthenticatedKey) -> None:
    if key.role != ApiKeyRole.ADMIN:
        raise _ADMIN_ONLY_ERROR


@router.post("/v1/invites", response_model=InviteCreateResponse, status_code=201)
async def create_invite(
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> InviteCreateResponse:
    _require_admin(key)

    plaintext_code = generate_invite_code()
    new_invite = Invite(
        id=uuid.uuid4(),
        organization_id=key.organization_id,
        code_hash=hash_api_key(plaintext_code),
        code_suffix=plaintext_code[-4:],
        created_by=key.id,
        # SQLAlchemy's server_default (created_at) only applies at flush,
        # not at object construction — the response below is built right
        # after db.add()/commit(), so setting it explicitly here avoids
        # the same "column comes back None pre-flush" bug API-08/API-10
        # already hit for their own rows' id/is_active/created_at.
        created_at=datetime.now(UTC),
    )
    db.add(new_invite)
    await db.commit()

    return InviteCreateResponse(
        id=new_invite.id,
        code_suffix=new_invite.code_suffix,
        used_at=None,
        used_by_email=None,
        created_at=new_invite.created_at,
        code=plaintext_code,
    )


@router.get("/v1/invites", response_model=list[InviteResponse])
async def list_invites(
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> list[InviteResponse]:
    _require_admin(key)

    rows = (await db.execute(select(Invite).order_by(Invite.created_at.desc()))).scalars().all()

    return [
        InviteResponse(
            id=row.id,
            code_suffix=row.code_suffix,
            used_at=row.used_at,
            used_by_email=row.used_by_email,
            created_at=row.created_at,
        )
        for row in rows
    ]
