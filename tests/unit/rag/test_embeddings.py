"""Unit tests for embed_chunks. The OpenAI-compatible client is always
mocked — no real Ollama or OpenAI call in these tests.
"""

import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from regradar.models.chunk import FilingChunk
from regradar.rag.chunking import Chunk
from regradar.rag.embeddings import EmbeddingError, embed_chunks


def _make_chunks(count: int) -> list[Chunk]:
    return [
        Chunk(
            chunk_index=i,
            chunk_text=f"chunk text {i}",
            section_reference=None,
            token_count=3,
            is_table=False,
        )
        for i in range(count)
    ]


def _mock_embedding_response(count: int) -> MagicMock:
    response = MagicMock()
    response.data = [MagicMock(embedding=[0.1] * 768) for _ in range(count)]
    response.usage = MagicMock(total_tokens=30)
    return response


async def test_embed_chunks_inserts_rows_with_embeddings() -> None:
    filing_id = uuid.uuid4()
    chunks = _make_chunks(2)
    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = _mock_embedding_response(2)
    mock_db = AsyncMock()
    mock_db.add_all = MagicMock()

    with patch(
        "regradar.rag.embeddings._get_embedding_client",
        return_value=(mock_client, "nomic-embed-text"),
    ):
        await embed_chunks(filing_id, chunks, mock_db)

    mock_db.add_all.assert_called_once()
    added_rows = mock_db.add_all.call_args[0][0]
    assert len(added_rows) == 2
    assert all(isinstance(row, FilingChunk) for row in added_rows)
    assert all(row.filing_id == filing_id for row in added_rows)
    assert all(row.embedding == [0.1] * 768 for row in added_rows)
    assert [row.chunk_index for row in added_rows] == [0, 1]
    mock_db.commit.assert_awaited_once()


async def test_embed_chunks_batches_at_100() -> None:
    filing_id = uuid.uuid4()
    chunks = _make_chunks(150)
    mock_client = MagicMock()
    mock_client.embeddings.create.side_effect = [
        _mock_embedding_response(100),
        _mock_embedding_response(50),
    ]
    mock_db = AsyncMock()
    mock_db.add_all = MagicMock()

    with patch(
        "regradar.rag.embeddings._get_embedding_client",
        return_value=(mock_client, "nomic-embed-text"),
    ):
        await embed_chunks(filing_id, chunks, mock_db)

    assert mock_client.embeddings.create.call_count == 2
    first_call_texts = mock_client.embeddings.create.call_args_list[0].kwargs["input"]
    second_call_texts = mock_client.embeddings.create.call_args_list[1].kwargs["input"]
    assert len(first_call_texts) == 100
    assert len(second_call_texts) == 50


async def test_embed_chunks_retries_failed_batch_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    filing_id = uuid.uuid4()
    chunks = _make_chunks(1)
    mock_client = MagicMock()
    mock_client.embeddings.create.side_effect = [
        RuntimeError("connection failed"),
        _mock_embedding_response(1),
    ]
    mock_db = AsyncMock()
    mock_db.add_all = MagicMock()

    with patch(
        "regradar.rag.embeddings._get_embedding_client",
        return_value=(mock_client, "nomic-embed-text"),
    ):
        await embed_chunks(filing_id, chunks, mock_db)

    assert mock_client.embeddings.create.call_count == 2
    mock_db.commit.assert_awaited_once()


async def test_embed_chunks_raises_after_all_retries_fail_and_never_touches_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    filing_id = uuid.uuid4()
    chunks = _make_chunks(1)
    mock_client = MagicMock()
    mock_client.embeddings.create.side_effect = RuntimeError("connection failed")
    mock_db = AsyncMock()
    mock_db.add_all = MagicMock()

    with patch(
        "regradar.rag.embeddings._get_embedding_client",
        return_value=(mock_client, "nomic-embed-text"),
    ):
        with pytest.raises(EmbeddingError):
            await embed_chunks(filing_id, chunks, mock_db)

    assert mock_client.embeddings.create.call_count == 3
    mock_db.add_all.assert_not_called()
    mock_db.commit.assert_not_awaited()


async def test_embed_chunks_does_nothing_for_empty_chunk_list() -> None:
    mock_db = AsyncMock()
    mock_db.add_all = MagicMock()

    await embed_chunks(uuid.uuid4(), [], mock_db)

    mock_db.add_all.assert_not_called()
    mock_db.commit.assert_not_awaited()
