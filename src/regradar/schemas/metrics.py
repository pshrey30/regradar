"""Pydantic response models for GET /v1/metrics."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from regradar.models.enums import EvalRunType


class MetricValue(BaseModel):
    value: float | None
    target: float | None


class MetricsResponse(BaseModel):
    id: uuid.UUID
    run_type: EvalRunType
    prompt_version: str | None
    git_commit_sha: str | None
    passed: bool
    created_at: datetime
    ragas_faithfulness: MetricValue
    ragas_context_recall: MetricValue
    rouge_l: MetricValue
    alert_precision: MetricValue
    alert_recall: MetricValue
    hallucination_rate: MetricValue
    extraction_f1: MetricValue
    p99_latency_ms: MetricValue
    avg_cost_per_filing_usd: MetricValue
