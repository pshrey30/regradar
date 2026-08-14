"""Embed AGENT-04's Chunk output via a local Ollama model (or real OpenAI,
untested/unexercised for now) and persist as filing_chunks rows.

embed_chunks's signature deviates from the literal ticket text
(list[Chunk] -> None) — filing_id and db are required because nothing
else inserts filing_chunks rows for these chunks; this function owns
that step too.
"""

import logging
import time
import uuid
from uuid import UUID

from openai import OpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.core.config import get_settings
from regradar.models.chunk import FilingChunk
from regradar.rag.chunking import Chunk

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
MAX_ATTEMPTS = 3


class EmbeddingError(Exception):
    """Raised when embedding a batch fails after retrying twice."""


def _get_embedding_client() -> tuple[OpenAI, str]:
    settings = get_settings()
    if settings.use_local_embeddings:
        return (
            OpenAI(base_url=settings.local_llm_base_url, api_key="ollama-local"),
            settings.local_embedding_model,
        )
    return OpenAI(api_key=settings.openai_api_key.get_secret_value()), "text-embedding-3-small"


def _embed_batch(client: OpenAI, model: str, texts: list[str]) -> list[list[float]]:
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt > 0:
            time.sleep(2**attempt)
        try:
            response = client.embeddings.create(model=model, input=texts)
            token_count = response.usage.total_tokens if response.usage else None
            logger.info(
                "Embedding batch: model=%s size=%d tokens=%s", model, len(texts), token_count
            )
            return [item.embedding for item in response.data]
        except Exception as exc:  # noqa: BLE001 — any failure retries, then raises EmbeddingError
            last_error = exc
            logger.warning("Embedding batch attempt %d failed: %s", attempt + 1, exc)

    raise EmbeddingError(f"Embedding failed after retries: {last_error}") from last_error


async def embed_chunks(filing_id: UUID, chunks: list[Chunk], db: AsyncSession) -> None:
    """Embed every chunk, then insert filing_chunks rows with embeddings
    already populated, in a single commit. If embedding fails, nothing
    is added to the session and the database is never touched — no
    partial state can exist.
    """
    if not chunks:
        return

    client, model = _get_embedding_client()

    embeddings: list[list[float]] = []
    for batch_start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[batch_start : batch_start + BATCH_SIZE]
        texts = [c.chunk_text for c in batch]
        embeddings.extend(_embed_batch(client, model, texts))

    rows = [
        FilingChunk(
            id=uuid.uuid4(),
            filing_id=filing_id,
            chunk_index=chunk.chunk_index,
            chunk_text=chunk.chunk_text,
            section_reference=chunk.section_reference,
            token_count=chunk.token_count,
            is_table=chunk.is_table,
            embedding=embedding,
        )
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]
    db.add_all(rows)
    await db.commit()
