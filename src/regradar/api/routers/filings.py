"""GET /v1/filings — paginated, filterable list of completed filings.

Only filings with status="complete" are returned (they're the only ones
with a real Brief row to source executive_brief from). An Executive-role
caller's effective risk filter is always intersected with {HIGH, CRITICAL}
— silently narrowed, never a 403 — per the Security & Access Document's
permission matrix (the ticket's own AI Coding Prompt only describes the
field-level restriction, which this endpoint's response schema already
satisfies by construction: it never includes extraction data for any
role).
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from openai import APIConnectionError, InternalServerError, RateLimitError
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.deps import AuthenticatedKey
from regradar.api.errors import ApiError
from regradar.api.middleware.rate_limit import enforce_rate_limit
from regradar.core.db import get_db
from regradar.llm_routing.tiered_router import build_client, select_model
from regradar.models.brief import Brief
from regradar.models.enums import ApiKeyRole, FilingDomain, FilingStatus, RiskLevel
from regradar.models.extraction import Extraction
from regradar.models.filing import Filing
from regradar.rag.retriever import retrieve_similar_filings
from regradar.schemas.filings import (
    FilingListItem,
    FilingListResponse,
    PersonaBriefResponse,
    SearchRequest,
    SearchResponse,
    SearchSource,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_SEARCH_EXCERPT_MAX_CHARS = 300

_SEARCH_SYSTEM_PROMPT = (
    "You are a regulatory compliance research assistant. Answer the user's question using "
    "ONLY the numbered excerpts provided below — never invent facts not present in them. "
    "If the excerpts don't contain enough information to answer, say so plainly. Keep the "
    "answer concise and factual."
)

_EXTRACTION_VISIBLE_ROLES = {
    ApiKeyRole.ADMIN,
    ApiKeyRole.ANALYST,
    ApiKeyRole.LEGAL_COUNSEL,
    ApiKeyRole.ENG_LEAD,
}

_EXECUTIVE_ALLOWED_RISK_LEVELS = {RiskLevel.HIGH, RiskLevel.CRITICAL}


def _build_filters(
    *,
    role: ApiKeyRole,
    domain: FilingDomain | None,
    risk: RiskLevel | None,
    since: datetime | None,
) -> list:
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)

    filters: list = [Filing.status == FilingStatus.COMPLETE]

    if domain is not None:
        filters.append(Filing.domain == domain)

    if role == ApiKeyRole.EXECUTIVE:
        # Intersect the requested risk (or "any") with the Executive-allowed
        # set. An empty intersection produces Filing.risk_level.in_(set())
        # which SQLAlchemy compiles to an always-false clause — an empty
        # result, not an error.
        effective_risk = (
            {risk} & _EXECUTIVE_ALLOWED_RISK_LEVELS
            if risk is not None
            else _EXECUTIVE_ALLOWED_RISK_LEVELS
        )
        filters.append(Filing.risk_level.in_(effective_risk))
    elif risk is not None:
        filters.append(Filing.risk_level == risk)

    if since is not None:
        filters.append(Filing.published_at >= since)

    return filters


@router.get("/v1/filings", response_model=FilingListResponse)
async def list_filings(
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    domain: FilingDomain | None = Query(default=None),
    risk: RiskLevel | None = Query(default=None),
    since: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1, le=100_000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> FilingListResponse:
    filters = _build_filters(role=key.role, domain=domain, risk=risk, since=since)

    total_stmt = (
        select(func.count())
        .select_from(Filing)
        .join(Brief, Brief.filing_id == Filing.id)
        .where(and_(*filters))
    )
    total = (await db.execute(total_stmt)).scalar_one()

    page_stmt = (
        select(Filing, Brief.executive_brief)
        .join(Brief, Brief.filing_id == Filing.id)
        .where(and_(*filters))
        .order_by(Filing.published_at.desc(), Filing.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = (await db.execute(page_stmt)).all()

    data = [
        FilingListItem(
            id=filing.id,
            entity_name=filing.entity_name,
            filing_type=filing.filing_type,
            domain=filing.domain,
            risk_level=filing.risk_level,
            published_at=filing.published_at,
            executive_brief=executive_brief,
        )
        for filing, executive_brief in rows
    ]

    return FilingListResponse(data=data, page=page, page_size=page_size, total=total)


@router.get("/v1/filings/{filing_id}")
async def get_filing(
    filing_id: uuid.UUID,
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Full single-filing record: brief, extraction, risk score, similar filings.

    extraction is entirely absent from the response for the Executive role
    (not merely null) — that's why this route builds a plain dict instead
    of using a fixed response_model, which can't express "key sometimes
    doesn't exist" the way a dict naturally can.
    """
    filing = await db.get(Filing, filing_id)
    if filing is None:
        raise ApiError(status_code=404, code="filing_not_found", message="No filing exists with this ID.")

    brief = (
        await db.execute(select(Brief).where(Brief.filing_id == filing_id))
    ).scalar_one_or_none()
    extraction = (
        await db.execute(select(Extraction).where(Extraction.filing_id == filing_id))
    ).scalar_one_or_none()

    similar_filings: list[dict] = []
    if extraction is not None and extraction.similar_filing_ids:
        similar_ids = [uuid.UUID(raw_id) for raw_id in extraction.similar_filing_ids]
        similar_rows = (
            await db.execute(select(Filing).where(Filing.id.in_(similar_ids)))
        ).scalars().all()
        similar_filings = [
            {
                "id": similar.id,
                "entity_name": similar.entity_name,
                "filing_type": similar.filing_type,
                "published_at": similar.published_at,
            }
            for similar in similar_rows
        ]

    response: dict = {
        "id": filing.id,
        "entity_name": filing.entity_name,
        "filing_type": filing.filing_type,
        "domain": filing.domain,
        "risk_level": filing.risk_level,
        "priority_score": filing.priority_score,
        "published_at": filing.published_at,
        "status": filing.status,
        "brief": {"executive_brief": brief.executive_brief} if brief is not None else None,
        "similar_filings": similar_filings,
    }

    if key.role in _EXTRACTION_VISIBLE_ROLES:
        response["extraction"] = (
            {
                "obligations": extraction.obligations,
                "deadlines": extraction.deadlines,
                "risk_flags": extraction.risk_flags,
                "affected_products": extraction.affected_products,
                "key_entities": extraction.key_entities,
                "competitor_mentions": extraction.competitor_mentions,
            }
            if extraction is not None
            else None
        )

    return response


def _synthesize_answer(query: str, sources: list[SearchSource]) -> str | None:
    """One LLM call synthesizing an answer from already-retrieved excerpts.

    Returns None (never raises) on any provider failure — callers fall back
    to keyword/vector-only results with a degraded=True flag rather than
    failing the whole request.
    """
    context = "\n\n".join(
        f"[{i}] ({source.entity_name}): {source.excerpt}" for i, source in enumerate(sources, 1)
    )
    choice = select_model(risk_level=None, task="analysis")
    client = build_client(choice)
    try:
        response = client.chat.completions.create(
            model=choice.model,
            messages=[
                {"role": "system", "content": _SEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": f"Excerpts:\n{context}\n\nQuestion: {query}"},
            ],
        )
    except (APIConnectionError, RateLimitError, InternalServerError):
        logger.warning("Search answer-generation call failed; degrading to sources only.")
        return None
    return response.choices[0].message.content


@router.post("/v1/filings/search", response_model=SearchResponse)
async def search_filings(
    body: SearchRequest,
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """Natural-language search across past filings (Legal Counsel persona's
    "what has the SEC said about X" use case). Not available to Executive.
    """
    if key.role == ApiKeyRole.EXECUTIVE:
        raise ApiError(
            status_code=403,
            code="forbidden",
            message="The executive role cannot use natural-language search.",
        )

    # No "current filing" to exclude — pass a random UUID that can never
    # match a real filing_id, so retrieve_similar_filings' exclusion filter
    # is effectively a no-op here.
    chunks = await retrieve_similar_filings(
        query_text=body.query, exclude_filing_id=uuid.uuid4(), db=db, top_k=body.top_k
    )
    if not chunks:
        return SearchResponse(answer="No relevant filings were found for this query.", sources=[])

    filing_ids = {chunk.filing_id for chunk in chunks}
    filing_rows = (
        await db.execute(select(Filing).where(Filing.id.in_(filing_ids)))
    ).scalars().all()
    entity_names = {filing.id: filing.entity_name for filing in filing_rows}

    sources = [
        SearchSource(
            filing_id=chunk.filing_id,
            excerpt=chunk.chunk_text[:_SEARCH_EXCERPT_MAX_CHARS],
            entity_name=entity_names.get(chunk.filing_id, "Unknown"),
        )
        for chunk in chunks
    ]

    answer = _synthesize_answer(body.query, sources)
    return SearchResponse(answer=answer, sources=sources, degraded=answer is None)


_VALID_PERSONAS = {"cco", "analyst", "engineer"}


@router.get("/v1/filings/{filing_id}/brief", response_model=PersonaBriefResponse)
async def get_filing_brief(
    filing_id: uuid.UUID,
    persona: str | None = Query(default=None),
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
) -> PersonaBriefResponse:
    """One persona's brief text. Omitting persona defaults to the
    executive brief. An Executive-role caller is always served the cco
    summary regardless of what persona value it requests — silently
    narrowed, the same pattern API-04 uses for Executive's risk filter —
    but a genuinely invalid persona value still 422s for every role,
    validated before the role-based narrowing is applied.
    """
    if persona is not None and persona not in _VALID_PERSONAS:
        raise ApiError(
            status_code=422,
            code="invalid_persona",
            message=f"persona must be one of: {', '.join(sorted(_VALID_PERSONAS))}",
        )

    filing = await db.get(Filing, filing_id)
    if filing is None:
        raise ApiError(status_code=404, code="filing_not_found", message="No filing exists with this ID.")

    brief = (
        await db.execute(select(Brief).where(Brief.filing_id == filing_id))
    ).scalar_one_or_none()
    if brief is None:
        raise ApiError(
            status_code=404, code="brief_not_found", message="No brief exists yet for this filing."
        )

    effective_persona = "cco" if key.role == ApiKeyRole.EXECUTIVE else persona

    summary_by_persona = {
        None: brief.executive_brief,
        "cco": brief.cco_summary,
        "analyst": brief.analyst_summary,
        "engineer": brief.engineer_summary,
    }
    return PersonaBriefResponse(
        persona=effective_persona or "executive",
        summary=summary_by_persona[effective_persona],
    )
