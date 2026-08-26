"""POST/GET /v1/webhooks, DELETE /v1/webhooks/{id} — webhook registration.

Ownership: a key can only see/manage webhooks it created, unless its role
is Admin (per the Security & Access Document's webhook permission rule —
the same "role-based override" shape API-04/API-07 already established
for Executive, just for Admin instead). DELETE returns 404, not 403, for
a webhook that exists but belongs to someone else, so the endpoint never
confirms that ID's existence to a caller who shouldn't see it.
"""

import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.deps import AuthenticatedKey
from regradar.api.errors import ApiError
from regradar.api.middleware.rate_limit import enforce_rate_limit, get_authenticated_db
from regradar.delivery.webhook_dispatcher import WebhookValidationError, validate_webhook_url
from regradar.models.enums import ApiKeyRole
from regradar.models.webhook import Webhook
from regradar.schemas.webhooks import WebhookCreateRequest, WebhookCreateResponse, WebhookResponse

router = APIRouter()


def _generate_hmac_secret() -> str:
    """32+ random bytes, base64url-encoded — per the ticket's own spec."""
    return secrets.token_urlsafe(32)


@router.post("/v1/webhooks", response_model=WebhookCreateResponse, status_code=201)
async def create_webhook(
    body: WebhookCreateRequest,
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> WebhookCreateResponse:
    try:
        validate_webhook_url(body.url)
    except WebhookValidationError as exc:
        raise ApiError(status_code=422, code="invalid_webhook_url", message=str(exc)) from exc

    hmac_secret = _generate_hmac_secret()
    # SQLAlchemy's column defaults (id, is_active, created_at) only apply at
    # flush, not at construction — set them explicitly so the response
    # built right after db.add() below reflects real values, not None.
    webhook = Webhook(
        id=uuid.uuid4(),
        api_key_id=key.id,
        url=body.url,
        hmac_secret=hmac_secret,
        is_active=True,
        filter_domain=body.filter_domain,
        filter_min_risk=body.filter_min_risk,
        created_at=datetime.now(UTC),
    )
    db.add(webhook)
    await db.commit()

    return WebhookCreateResponse(
        id=webhook.id,
        url=webhook.url,
        is_active=webhook.is_active,
        filter_domain=webhook.filter_domain,
        filter_min_risk=webhook.filter_min_risk,
        created_at=webhook.created_at,
        hmac_secret=hmac_secret,
    )


@router.get("/v1/webhooks", response_model=list[WebhookResponse])
async def list_webhooks(
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> list[WebhookResponse]:
    stmt = select(Webhook)
    if key.role != ApiKeyRole.ADMIN:
        stmt = stmt.where(Webhook.api_key_id == key.id)
    rows = (await db.execute(stmt)).scalars().all()

    return [
        WebhookResponse(
            id=row.id,
            url=row.url,
            is_active=row.is_active,
            filter_domain=row.filter_domain,
            filter_min_risk=row.filter_min_risk,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.delete("/v1/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: uuid.UUID,
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_authenticated_db),
) -> Response:
    webhook = await db.get(Webhook, webhook_id)
    if webhook is None or (webhook.api_key_id != key.id and key.role != ApiKeyRole.ADMIN):
        # Same 404 either way — a caller who doesn't own this webhook can't
        # distinguish "doesn't exist" from "exists but isn't yours".
        raise ApiError(status_code=404, code="webhook_not_found", message="No webhook exists with this ID.")

    await db.delete(webhook)
    await db.commit()
    return Response(status_code=204)
