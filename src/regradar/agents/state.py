"""Pydantic state schema shared by every node in the LangGraph pipeline.

Every AGENT-* ticket after this one reads from and writes to
`PipelineState` — it's the one shape that flows all the way from
ingestion hand-off through triage, retrieval, analysis, summarization,
and delivery.
"""

import uuid

from pydantic import BaseModel

from regradar.models.enums import FilingDomain, RiskLevel
from regradar.rag.chunking import Chunk


class RetrievedChunk(BaseModel):
    """One chunk returned by the RAG retrieval agent (AGENT-06)."""

    filing_id: uuid.UUID
    chunk_text: str
    score: float


class ExtractionResult(BaseModel):
    """Structured output of the Analysis Agent (AGENT-07).

    Field names mirror the `extractions` table's columns 1:1 so this can
    be written to the ORM model via `Extraction(**result.model_dump())`.
    """

    obligations: list[dict] = []
    deadlines: list[dict] = []
    risk_flags: list[str] = []
    affected_products: list[str] = []
    key_entities: list[str] = []
    competitor_mentions: list[str] = []
    model_used: str | None = None


class BriefSet(BaseModel):
    """The four persona briefs produced by the Summarization Agent (AGENT-08).

    Field names mirror the `briefs` table's columns 1:1.
    """

    executive_brief: str
    cco_summary: str
    analyst_summary: str
    engineer_summary: str
    model_used: str | None = None


class PipelineState(BaseModel):
    """Everything that flows through the LangGraph pipeline for one filing."""

    filing_id: uuid.UUID
    raw_text: str
    domain: FilingDomain | None = None
    risk_level: RiskLevel | None = None
    classification_confidence: float | None = None
    retrieved_chunks: list[RetrievedChunk] | None = None
    chunks: list[Chunk] | None = None
    extraction: ExtractionResult | None = None
    briefs: BriefSet | None = None
    delivery_status: str | None = None
    delivery_success: bool | None = None
