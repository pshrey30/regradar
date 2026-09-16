"""Live verification (marked @pytest.mark.live) that a real LLM call
through relevance_agent, given an org profile with a deliberately
matching watchlist entity, actually names that match in the rationale —
not just a plausible-sounding generic string."""

import uuid

import pytest

from regradar.agents.relevance_agent import relevance_node
from regradar.agents.state import ExtractionResult, OrgProfileSnapshot, PipelineState
from regradar.models.enums import FilingDomain, RiskLevel

pytestmark = pytest.mark.live


def test_relevance_node_names_the_matched_watchlist_entity_against_real_llm() -> None:
    state = PipelineState(
        filing_id=uuid.uuid4(),
        raw_text="Acme Corp recalled its insulin pump line due to a battery defect.",
        domain=FilingDomain.CLINICAL,
        risk_level=RiskLevel.HIGH,
        extraction=ExtractionResult(
            obligations=[],
            deadlines=[],
            risk_flags=["recall"],
            affected_products=["insulin pumps"],
            key_entities=["Acme Corp"],
            competitor_mentions=["Acme Corp"],
        ),
        org_profile=OrgProfileSnapshot(
            industry="medical device manufacturing",
            watchlist_entities=["Acme Corp"],
            products=["insulin pumps"],
        ),
    )
    result = relevance_node(state)
    assert result.relevance is not None
    assert "acme" in result.relevance.rationale.lower()
