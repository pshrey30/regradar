"""Unit tests for retrieve_similar_filings. The DB query and the query
embedding call are both mocked — BM25 and fusion run for real in-memory
(no external call needed for either).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from regradar.agents.state import RetrievedChunk
from regradar.rag.retriever import retrieve_similar_filings


def _make_chunk_row(filing_id, chunk_id, text, embedding):
    row = MagicMock()
    row.id = chunk_id
    row.filing_id = filing_id
    row.chunk_text = text
    row.embedding = embedding
    return row


async def test_retrieve_similar_filings_returns_empty_list_for_empty_corpus() -> None:
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await retrieve_similar_filings(
        "some query text", uuid.uuid4(), mock_db, top_k=5
    )

    assert result == []


async def test_retrieve_similar_filings_excludes_current_filing_via_query() -> None:
    exclude_id = uuid.uuid4()
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    await retrieve_similar_filings("query", exclude_id, mock_db, top_k=5)

    mock_db.execute.assert_awaited_once()
    executed_stmt = mock_db.execute.call_args[0][0]
    compiled = str(executed_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert exclude_id.hex in compiled.replace("-", "")


async def test_retrieve_similar_filings_deduplicates_to_one_chunk_per_filing() -> None:
    filing_a = uuid.uuid4()
    filing_b = uuid.uuid4()
    rows = [
        _make_chunk_row(
            filing_a, uuid.uuid4(), "Material weakness in internal controls disclosed.", [0.1] * 768
        ),
        _make_chunk_row(
            filing_a, uuid.uuid4(), "Routine quarterly filing text.", [0.05] * 768
        ),
        _make_chunk_row(
            filing_b, uuid.uuid4(), "FDA warning letter regarding manufacturing.", [0.2] * 768
        ),
    ]
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rows
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 768)]
    )

    with patch(
        "regradar.rag.retriever._get_embedding_client",
        return_value=(mock_client, "nomic-embed-text"),
    ):
        result = await retrieve_similar_filings(
            "material weakness in internal controls", uuid.uuid4(), mock_db, top_k=5
        )

    result_filing_ids = [r.filing_id for r in result]
    assert len(result_filing_ids) == len(set(result_filing_ids))
    assert filing_a in result_filing_ids
    assert filing_b in result_filing_ids


async def test_retrieve_similar_filings_respects_top_k() -> None:
    rows = [
        _make_chunk_row(uuid.uuid4(), uuid.uuid4(), f"Filing text number {i}.", [0.1 * i] * 768)
        for i in range(10)
    ]
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rows
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 768)]
    )

    with patch(
        "regradar.rag.retriever._get_embedding_client",
        return_value=(mock_client, "nomic-embed-text"),
    ):
        result = await retrieve_similar_filings(
            "filing text", uuid.uuid4(), mock_db, top_k=3
        )

    assert len(result) <= 3


async def test_retrieve_similar_filings_returns_retrieved_chunk_objects() -> None:
    filing_a = uuid.uuid4()
    rows = [
        _make_chunk_row(
            filing_a, uuid.uuid4(), "Material weakness in internal controls disclosed.", [0.1] * 768
        ),
    ]
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rows
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 768)]
    )

    with patch(
        "regradar.rag.retriever._get_embedding_client",
        return_value=(mock_client, "nomic-embed-text"),
    ):
        result = await retrieve_similar_filings(
            "material weakness in internal controls", uuid.uuid4(), mock_db, top_k=5
        )

    assert len(result) == 1
    assert isinstance(result[0], RetrievedChunk)
    assert result[0].filing_id == filing_a
    assert result[0].chunk_text == "Material weakness in internal controls disclosed."
    assert isinstance(result[0].score, float)
