"""EVAL-05 — pushes every prompt this project's agents use into LangSmith's
Prompt Hub, giving them one central, versioned home instead of only living
as Python string constants scattered across four agent modules.

Fails open with no LANGSMITH_API_KEY configured — matches this project's
convention for every other optional external integration (e.g.
core/redis_client.py's rate-limit fail-open, delivery clients' per-channel
failure isolation): a missing credential degrades to "prompts weren't
pushed," never a crash. Each prompt is pushed independently too — one
prompt's push failure doesn't stop the others from being attempted.

Only the system prompt half of each agent is pushed. The user-message half
of search/summarization/extraction is built from an f-string helper at call
time (retrieved chunks, extracted obligations, etc.), not a fixed template —
representing that as a LangSmith prompt would mean inventing placeholder
variables no real call site ever fills in the same shape twice. Triage's
spot-check prompt is the one exception with a genuinely reusable, fixed
`{text}`-parameterized user template, so both halves are pushed for it.
"""

import logging

from langchain_core.prompts import ChatPromptTemplate
from langsmith import Client

from regradar.agents.analysis_agent import EXTRACTION_SYSTEM_PROMPT
from regradar.agents.analysis_agent import PROMPT_VERSION as EXTRACTION_PROMPT_VERSION
from regradar.agents.summarization_agent import PROMPT_VERSION as SUMMARIZATION_PROMPT_VERSION
from regradar.agents.summarization_agent import SUMMARIZATION_SYSTEM_PROMPT
from regradar.agents.triage_agent import PROMPT_VERSION as TRIAGE_PROMPT_VERSION
from regradar.agents.triage_agent import SPOT_CHECK_SYSTEM_PROMPT, SPOT_CHECK_USER_PROMPT_TEMPLATE
from regradar.core.config import get_settings
from regradar.rag.answer_synthesis import PROMPT_VERSION as SEARCH_PROMPT_VERSION
from regradar.rag.answer_synthesis import SEARCH_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _prompt_registry() -> dict[str, tuple[ChatPromptTemplate, str]]:
    """{LangSmith prompt identifier: (template, local PROMPT_VERSION)}."""
    return {
        "regradar-search": (
            ChatPromptTemplate.from_messages([("system", SEARCH_SYSTEM_PROMPT)]),
            SEARCH_PROMPT_VERSION,
        ),
        "regradar-summarization": (
            ChatPromptTemplate.from_messages([("system", SUMMARIZATION_SYSTEM_PROMPT)]),
            SUMMARIZATION_PROMPT_VERSION,
        ),
        "regradar-extraction": (
            ChatPromptTemplate.from_messages([("system", EXTRACTION_SYSTEM_PROMPT)]),
            EXTRACTION_PROMPT_VERSION,
        ),
        "regradar-triage-spot-check": (
            ChatPromptTemplate.from_messages(
                [("system", SPOT_CHECK_SYSTEM_PROMPT), ("user", SPOT_CHECK_USER_PROMPT_TEMPLATE)]
            ),
            TRIAGE_PROMPT_VERSION,
        ),
    }


def get_langsmith_client() -> Client | None:
    settings = get_settings()
    if settings.langsmith_api_key is None:
        return None
    return Client(api_key=settings.langsmith_api_key.get_secret_value())


def push_all_prompts() -> dict[str, str | None]:
    """Pushes every registered prompt to LangSmith's Prompt Hub.

    Returns {prompt_identifier: url_or_None} — None means that prompt
    wasn't pushed (no client configured, or that specific push failed;
    check logs for which). Never raises.
    """
    client = get_langsmith_client()
    if client is None:
        logger.warning(
            "LANGSMITH_API_KEY is not configured; skipping all prompt pushes."
        )
        return dict.fromkeys(_prompt_registry())

    results: dict[str, str | None] = {}
    for identifier, (template, version) in _prompt_registry().items():
        try:
            results[identifier] = client.push_prompt(
                identifier,
                object=template,
                commit_description=f"regradar {identifier} — version {version}",
                tags=[version],
            )
        except Exception:
            logger.warning("Failed to push prompt %r to LangSmith.", identifier, exc_info=True)
            results[identifier] = None
    return results
