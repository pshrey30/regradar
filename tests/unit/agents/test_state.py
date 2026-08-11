"""Unit tests for the shared PipelineState schema."""

import uuid

import pytest
from pydantic import ValidationError

from regradar.agents.state import BriefSet, ExtractionResult, PipelineState, RetrievedChunk
from regradar.models.enums import FilingDomain, RiskLevel


def test_pipeline_state_requires_filing_id_and_raw_text() -> None:
    with pytest.raises(ValidationError):
        PipelineState()  # type: ignore[call-arg]


def test_pipeline_state_minimal_construction_has_none_defaults() -> None:
    filing_id = uuid.uuid4()
    state = PipelineState(filing_id=filing_id, raw_text="some filing text")

    assert state.filing_id == filing_id
    assert state.raw_text == "some filing text"
    assert state.domain is None
    assert state.risk_level is None
    assert state.classification_confidence is None
    assert state.retrieved_chunks is None
    assert state.extraction is None
    assert state.briefs is None
    assert state.delivery_status is None


def test_pipeline_state_accepts_fully_populated_fields() -> None:
    filing_id = uuid.uuid4()
    chunk = RetrievedChunk(filing_id=uuid.uuid4(), chunk_text="matched text", score=0.87)
    extraction = ExtractionResult(
        obligations=[{"description": "file a report", "source_citation": "chunk-3"}],
        deadlines=[{"description": "annual filing", "date": "2026-12-31"}],
        risk_flags=["material weakness"],
        affected_products=["Product X"],
        key_entities=["Acme Corp"],
        competitor_mentions=["Rival Inc"],
        model_used="gpt-4o",
    )
    briefs = BriefSet(
        executive_brief="Acme filed a report flagging a material weakness.",
        cco_summary="High risk: material weakness disclosed.",
        analyst_summary="- File annual report by 2026-12-31",
        engineer_summary="10-K | risk=high | ref: filing/123",
        model_used="gpt-4o",
    )

    state = PipelineState(
        filing_id=filing_id,
        raw_text="full filing text",
        domain=FilingDomain.FINANCIAL,
        risk_level=RiskLevel.HIGH,
        classification_confidence=0.93,
        retrieved_chunks=[chunk],
        extraction=extraction,
        briefs=briefs,
        delivery_status="sent",
    )

    assert state.domain == FilingDomain.FINANCIAL
    assert state.risk_level == RiskLevel.HIGH
    assert state.retrieved_chunks == [chunk]
    assert state.extraction == extraction
    assert state.briefs == briefs
    assert state.delivery_status == "sent"


def test_extraction_result_defaults_to_empty_lists() -> None:
    extraction = ExtractionResult()

    assert extraction.obligations == []
    assert extraction.deadlines == []
    assert extraction.risk_flags == []
    assert extraction.affected_products == []
    assert extraction.key_entities == []
    assert extraction.competitor_mentions == []
    assert extraction.model_used is None
