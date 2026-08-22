"""Unit tests for the real retrieve_node — retrieve_similar_filings is
mocked; this test only covers the node's own state-wiring logic."""

import uuid
from unittest.mock import AsyncMock, patch

from regradar.agents.rag_retrieval_agent import retrieve_node
from regradar.agents.state import PipelineState, RetrievedChunk


def _make_state() -> PipelineState:
    return PipelineState(filing_id=uuid.uuid4(), raw_text="Filing text about a material weakness.")


async def test_retrieve_node_populates_retrieved_chunks() -> None:
    state = _make_state()
    mock_db = AsyncMock()
    fake_chunks = [
        RetrievedChunk(filing_id=uuid.uuid4(), chunk_text="Similar filing text.", score=0.85)
    ]

    with patch(
        "regradar.agents.rag_retrieval_agent.retrieve_similar_filings",
        AsyncMock(return_value=fake_chunks),
    ) as mock_retrieve:
        result = await retrieve_node(state, {"configurable": {"db": mock_db}})

    mock_retrieve.assert_awaited_once_with(state.raw_text, state.filing_id, mock_db, top_k=5)
    assert result.retrieved_chunks == fake_chunks


async def test_retrieve_node_handles_empty_results() -> None:
    state = _make_state()
    mock_db = AsyncMock()

    with patch(
        "regradar.agents.rag_retrieval_agent.retrieve_similar_filings",
        AsyncMock(return_value=[]),
    ):
        result = await retrieve_node(state, {"configurable": {"db": mock_db}})

    assert result.retrieved_chunks == []
