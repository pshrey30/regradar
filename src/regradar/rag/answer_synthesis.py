"""Synthesizes a natural-language answer from retrieved filing excerpts.

Shared by API-06's POST /v1/filings/search route and EVAL-01's harness —
the harness evaluates exactly this code path, not a re-implementation of it.
"""

import logging
import time

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from regradar.llm_routing.tiered_router import build_client, select_model
from regradar.schemas.filings import SearchSource

logger = logging.getLogger(__name__)

SEARCH_EXCERPT_MAX_CHARS = 300

# One extra attempt beyond the OpenAI SDK's own built-in retries, on top of
# those — local models (Ollama) are slow to warm up on a cold start, which
# looks identical to a real provider failure to build_client's caller. A
# short, fixed backoff (not exponential) since this is a synchronous
# request in the critical path of a user-facing API call, not a background
# job — FE-05's live verification surfaced a real cold-start degrade that
# a single retry a couple seconds later would have avoided.
_MAX_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 2
_RETRYABLE_ERRORS = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)

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

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = client.chat.completions.create(
                model=choice.model,
                messages=[
                    {"role": "system", "content": SEARCH_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Excerpts:\n{context}\n\nQuestion: {query}"},
                ],
                # Every other agent in this codebase pins temperature=0
                # (triage_agent.py, summarization_agent.py, analysis_agent.py);
                # this call was the one exception, making EVAL-01's ragas
                # faithfulness/context_recall scores non-deterministic run to
                # run — confirmed via two live runs producing meaningfully
                # different scores off the same fixture set.
                temperature=0,
            )
        except _RETRYABLE_ERRORS:
            if attempt < _MAX_ATTEMPTS:
                logger.warning(
                    "Search answer-generation call failed (attempt %d/%d); retrying in %ds.",
                    attempt,
                    _MAX_ATTEMPTS,
                    _RETRY_DELAY_SECONDS,
                )
                time.sleep(_RETRY_DELAY_SECONDS)
                continue
            logger.warning(
                "Search answer-generation call failed after %d attempts; degrading to sources only.",
                _MAX_ATTEMPTS,
            )
            return None
        else:
            return response.choices[0].message.content
    return None  # unreachable — satisfies mypy's exhaustiveness check
