"""Hybrid BM25 + pgvector retrieval — finds past filings' chunks similar
to a new filing, fused via LlamaIndex's QueryFusionRetriever.

Dense retrieval is a custom BaseRetriever wrapping a direct pgvector
query against filing_chunks — NOT a LlamaIndex vector store, since those
expect to own their own table schema, incompatible with filing_chunks'
existing hand-built schema (FOUND-02/AGENT-05).
"""

from uuid import UUID

from llama_index.core.retrievers import BaseRetriever
from llama_index.core.retrievers.fusion_retriever import FUSION_MODES, QueryFusionRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from llama_index.llms.ollama import Ollama
from llama_index.retrievers.bm25 import BM25Retriever
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.agents.state import RetrievedChunk
from regradar.core.config import get_settings
from regradar.models.chunk import FilingChunk
from regradar.rag.embeddings import _get_embedding_client

DENSE_CANDIDATE_MULTIPLIER = 4


class DenseFilingChunkRetriever(BaseRetriever):
    """Wraps a pre-fetched, pre-scored list of (node, score) pairs from a
    pgvector cosine-distance query as a LlamaIndex retriever, so it can
    participate in QueryFusionRetriever alongside BM25."""

    def __init__(self, results: list[NodeWithScore]) -> None:
        self._results = results
        super().__init__()

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        return self._results


async def retrieve_similar_filings(
    query_text: str, exclude_filing_id: UUID, db: AsyncSession, top_k: int
) -> list[RetrievedChunk]:
    """Hybrid-retrieve the top_k most similar past filings' chunks.

    1. Embed query_text via the local Ollama client (same pattern as
       rag/embeddings.py's _get_embedding_client()).
    2. Query filing_chunks for the DENSE_CANDIDATE_MULTIPLIER * top_k
       nearest neighbors by cosine distance, excluding exclude_filing_id.
    3. Build a BM25Retriever over the same candidate set's text, and a
       DenseFilingChunkRetriever over their cosine-similarity scores.
    4. Fuse both rankings via QueryFusionRetriever (RELATIVE_SCORE mode,
       equal weights).
    5. Walk fused results in rank order, keeping the first (highest-
       ranked) chunk per distinct filing_id, until top_k distinct
       filings are collected.
    """
    settings = get_settings()

    # Check whether there's any corpus to search before embedding the
    # query — an empty corpus should never incur an embedding call.
    any_chunks_stmt = (
        select(FilingChunk.id).where(FilingChunk.filing_id != exclude_filing_id).limit(1)
    )
    any_chunks_result = await db.execute(any_chunks_stmt)
    if any_chunks_result.scalar_one_or_none() is None:
        return []

    client, model = _get_embedding_client()
    query_embedding = client.embeddings.create(model=model, input=[query_text]).data[0].embedding

    stmt = (
        select(FilingChunk)
        .where(FilingChunk.filing_id != exclude_filing_id)
        .order_by(FilingChunk.embedding.cosine_distance(query_embedding))
        .limit(top_k * DENSE_CANDIDATE_MULTIPLIER)
    )
    result = await db.execute(stmt)
    candidate_chunks = list(result.scalars().all())

    if not candidate_chunks:
        return []

    nodes = [
        TextNode(
            id_=str(chunk.id),
            text=chunk.chunk_text,
            metadata={"filing_id": str(chunk.filing_id)},
        )
        for chunk in candidate_chunks
    ]

    dense_results = [
        NodeWithScore(
            node=node,
            score=1.0 - _cosine_distance(chunk.embedding, query_embedding),
        )
        for node, chunk in zip(nodes, candidate_chunks, strict=True)
    ]
    dense_retriever = DenseFilingChunkRetriever(dense_results)

    bm25_retriever = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=len(nodes))

    local_llm = Ollama(
        model=settings.local_llm_model,
        base_url=settings.local_llm_base_url,
        request_timeout=30.0,
    )
    fusion = QueryFusionRetriever(
        retrievers=[bm25_retriever, dense_retriever],
        llm=local_llm,
        mode=FUSION_MODES.RELATIVE_SCORE,
        retriever_weights=[0.5, 0.5],
        similarity_top_k=len(nodes),
        num_queries=1,
        use_async=False,
    )
    fused = fusion.retrieve(query_text)

    seen_filing_ids: set[str] = set()
    output: list[RetrievedChunk] = []
    for item in fused:
        filing_id_str = item.node.metadata["filing_id"]
        if filing_id_str in seen_filing_ids:
            continue
        seen_filing_ids.add(filing_id_str)
        output.append(
            RetrievedChunk(
                filing_id=UUID(filing_id_str),
                chunk_text=item.node.text,
                score=item.score or 0.0,
            )
        )
        if len(output) >= top_k:
            break

    return output


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 1.0
    return 1.0 - (dot / (norm_a * norm_b))
