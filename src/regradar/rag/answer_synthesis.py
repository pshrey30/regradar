"""Synthesizes a natural-language answer from retrieved filing excerpts.

Shared by API-06's POST /v1/filings/search route and EVAL-01's harness —
the harness evaluates exactly this code path, not a re-implementation of it.
"""

import logging

from openai import APIConnectionError, InternalServerError, RateLimitError

from regradar.llm_routing.tiered_router import build_client, select_model
from regradar.schemas.filings import SearchSource

logger = logging.getLogger(__name__)

SEARCH_EXCERPT_MAX_CHARS = 300

# Bumped whenever SEARCH_SYSTEM_PROMPT changes meaningfully — EVAL-01's
# harness records this on every eval_runs row so a metrics regression can be
# traced to a specific prompt revision, not just a specific commit.
PROMPT_VERSION = "search-v1"

SEARCH_SYSTEM_PROMPT = (
    "You are a regulatory compliance research assistant. Answer the user's question using "
    "ONLY the numbered excerpts provided below — never invent facts not present in them. "
    "If the excerpts don't contain enough information to answer, say so plainly. Keep the "
    "answer concise and factual."
)


def synthesize_answer(query: str, sources: list[SearchSource]) -> str | None:
    """One LLM call synthesizing an answer from already-retrieved excerpts.

    Returns None (never raises) on any provider failure — callers fall back
    to keyword/vector-only results with a degraded=True flag rather than
    failing the whole request.
    """
    context = "\n\n".join(
        f"[{i}] ({source.entity_name}): {source.excerpt}" for i, source in enumerate(sources, 1)
    )
    choice = select_model(risk_level=None, task="analysis")
    client = build_client(choice)
    try:
        response = client.chat.completions.create(
            model=choice.model,
            messages=[
                {"role": "system", "content": SEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": f"Excerpts:\n{context}\n\nQuestion: {query}"},
            ],
        )
    except (APIConnectionError, RateLimitError, InternalServerError):
        logger.warning("Search answer-generation call failed; degrading to sources only.")
        return None
    return response.choices[0].message.content
