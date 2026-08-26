"""GET /v1/metrics — eval-quality/cost/latency snapshots, Admin/Eng Lead only.

The columns this reads (eval_runs) are populated by EVAL-01 (Ragas
harness) and EVAL-06 (Langfuse cost/latency dashboard) — neither is built
yet, so eval_runs is genuinely empty in every environment so far. This
endpoint is written to behave correctly against that real state (no data
yet is not an error for the history case, and a clean 404 for the
default/no-data case) rather than assuming rows already exist.

Target values are the project's own documented thresholds (PRD/ticket
list): ragas_faithfulness >0.87, ragas_context_recall >0.80, rouge_l
>0.45, extraction_f1 >0.82 (EVAL-01/02/04's stated targets), and
p99_latency_ms's target is LAUNCH-01's <3min filing-to-alert figure in
milliseconds. alert_precision/alert_recall's targets are derived from
EVAL-03's stated <5% false-positive / <3% false-negative rate targets
(expressed here as the equivalent precision/recall floor, since that's
what these two columns actually measure). hallucination_rate and
avg_cost_per_filing_usd have no documented numeric target anywhere in the
ticket list, so their target is null rather than an invented number.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.deps import AuthenticatedKey
from regradar.api.errors import ApiError
from regradar.api.middleware.rate_limit import enforce_rate_limit
from regradar.core.db import get_db
from regradar.models.enums import ApiKeyRole
from regradar.models.eval_run import EvalRun
from regradar.schemas.metrics import MetricsResponse, MetricValue

router = APIRouter()

_METRIC_TARGETS: dict[str, float | None] = {
    "ragas_faithfulness": 0.87,
    "ragas_context_recall": 0.80,
    "rouge_l": 0.45,
    "extraction_f1": 0.82,
    "alert_precision": 0.95,
    "alert_recall": 0.97,
    "p99_latency_ms": 180_000.0,
    "hallucination_rate": None,
    "avg_cost_per_filing_usd": None,
}

_METRIC_FIELDS = list(_METRIC_TARGETS.keys())


def _to_response(row: EvalRun) -> MetricsResponse:
    metric_kwargs = {
        field: MetricValue(
            value=getattr(row, field),
            target=_METRIC_TARGETS[field],
        )
        for field in _METRIC_FIELDS
    }
    return MetricsResponse(
        id=row.id,
        run_type=row.run_type,
        prompt_version=row.prompt_version,
        git_commit_sha=row.git_commit_sha,
        passed=row.passed,
        created_at=row.created_at,
        **metric_kwargs,
    )


@router.get("/v1/metrics")
async def get_metrics(
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    key: AuthenticatedKey = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
) -> MetricsResponse | list[MetricsResponse]:
    if key.role not in (ApiKeyRole.ADMIN, ApiKeyRole.ENG_LEAD):
        raise ApiError(
            status_code=403,
            code="forbidden",
            message="Only the Admin and Eng Lead roles can view eval metrics.",
        )

    if since is None and until is None:
        stmt = select(EvalRun).order_by(EvalRun.created_at.desc()).limit(1)
        row = (await db.execute(stmt)).scalars().first()
        if row is None:
            raise ApiError(
                status_code=404,
                code="no_eval_data",
                message="No eval run has been recorded yet.",
            )
        return _to_response(row)

    stmt = select(EvalRun).order_by(EvalRun.created_at.asc())
    if since is not None:
        stmt = stmt.where(EvalRun.created_at >= since)
    if until is not None:
        stmt = stmt.where(EvalRun.created_at <= until)
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_response(row) for row in rows]
