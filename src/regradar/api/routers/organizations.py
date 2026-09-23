"""GET/PUT /v1/organizations/me/profile — the org's OrganizationProfile
(industry, business description, watchlist entities, products, risk
priorities) that relevance_agent.py uses to score how much a filing
matters to *this* organization's business, not just how objectively
severe it is.

GET is readable by any authenticated role (matches organization_profiles'
RLS SELECT policy — migrations/versions/0027). PUT is Admin-only: this is
the org's shared business context, not a per-user setting.

0027's RLS policies grant the caller's own resolved role here directly —
Depends(get_authenticated_db) is sufficient, unlike tables whose policies
are still service-role-only.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.deps import AuthenticatedKey
from regradar.api.errors import ApiError
from regradar.api.middleware.rate_limit import enforce_rate_limit, get_authenticated_db
from regradar.core.db import set_rls_context
from regradar.models.enums import ApiKeyRole, FilingStatus
from regradar.models.filing import Filing
from regradar.models.organization_profile import OrganizationProfile, is_complete
from regradar.schemas.organization import OrganizationProfileRequest, OrganizationProfileResponse

router = APIRouter()

_ADMIN_ONLY_ERROR = ApiError(
    status_code=403,
    code="forbidden",
    message="Only the Admin role can update the organization profile.",
)


def _to_response(profile: OrganizationProfile | None) -> OrganizationProfileResponse:
    return OrganizationProfileResponse(
        industry=profile.industry if profile else None,
        business_description=profile.business_description if profile else None,
        watchlist_entities=list(profile.watchlist_entities) if profile else [],
        products=list(profile.products) if profile else [],
        risk_priorities=list(profile.risk_priorities) if profile else [],
        is_complete=is_complete(profile),
    )


@router.get("/v1/organizations/me/profile", response_model=OrganizationProfileResponse)
async def get_organization_profile(
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> OrganizationProfileResponse:
    profile = await db.get(OrganizationProfile, key.organization_id)
    return _to_response(profile)


@router.put("/v1/organizations/me/profile", response_model=OrganizationProfileResponse)
async def update_organization_profile(
    body: OrganizationProfileRequest,
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> OrganizationProfileResponse:
    if key.role != ApiKeyRole.ADMIN:
        raise _ADMIN_ONLY_ERROR

    profile = await db.get(OrganizationProfile, key.organization_id)
    if profile is None:
        profile = OrganizationProfile(organization_id=key.organization_id)
        db.add(profile)
    profile.industry = body.industry
    profile.business_description = body.business_description
    profile.watchlist_entities = body.watchlist_entities
    profile.products = body.products
    profile.risk_priorities = body.risk_priorities

    # Re-assert `service` role on this same session/transaction before the
    # `filings` write below: filings_write (migration 0009) is service-role
    # -only, so under the admin role this route otherwise runs as
    # (get_authenticated_db), the requeue UPDATE would silently affect zero
    # rows under RLS. Calling set_rls_context() here triggers SQLAlchemy's
    # default autoflush *before* the role actually switches — the profile
    # INSERT/UPDATE staged above is flushed first, still under the caller's
    # own `admin` role (which organization_profiles' write policy allows),
    # and only then does `app.current_role` become `service` for the
    # `filings` UPDATE that follows. One transaction, one commit — the role
    # is never reverted to admin, and no read after this point depends on
    # admin-only visibility.
    await set_rls_context(db, role="service")

    # Every filing parked waiting on this organization's setup gets the
    # same status flip fresh ingestion already uses (INGESTED) — the
    # existing `process-pending` path picks these up the normal way,
    # no separate re-trigger mechanism needed.
    await db.execute(
        update(Filing)
        .where(
            Filing.organization_id == key.organization_id,
            Filing.status == FilingStatus.NEEDS_ORGANIZATION_SETUP,
        )
        .values(status=FilingStatus.INGESTED)
    )
    await db.commit()

    return _to_response(profile)
