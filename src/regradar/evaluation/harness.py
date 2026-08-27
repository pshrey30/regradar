"""EVAL-01 — the Ragas eval harness.

Seeds a small, self-contained fixture corpus (evaluation/dataset.py) into
`filings`/`filing_chunks` under the default organization, runs each fixture
question through the *real* API-06 retrieval + answer-synthesis pipeline
(rag.retriever.retrieve_similar_filings, rag.answer_synthesis.synthesize_answer
— exactly what POST /v1/filings/search calls), scores each (question,
retrieved context, generated answer, reference answer) tuple with real Ragas
Faithfulness/ContextRecall metrics, averages them into one `eval_runs` row,
and always rolls back its fixture inserts afterward (never committed at
all) — a run never leaves synthetic filings visible to a real API caller.

The judge LLM is the same local-Ollama-or-real-OpenAI choice every other
LLM call in this project makes (llm_routing.tiered_router.select_model) —
no new provider, no new credential, consistent with ADR-05.

Only ragas_faithfulness/ragas_context_recall are populated here.
rouge_l/extraction_f1/alert_precision/alert_recall/hallucination_rate/
avg_cost_per_filing_usd/p99_latency_ms are EVAL-02/03/04/06's own scope,
deliberately left null (not zero — GET /v1/metrics distinguishes "not
measured" from "measured as zero" via `MetricValue.value` being nullable).
"""

import logging
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from openai import AsyncOpenAI
from ragas.llms import llm_factory
from ragas.metrics.collections import ContextRecall, Faithfulness
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.api.routers.metrics import METRIC_TARGETS
from regradar.core.db import get_session_factory, set_rls_context
from regradar.evaluation.dataset import EVAL_CASES, SEED_FILINGS, EvalCase
from regradar.llm_routing.tiered_router import select_model
from regradar.models.chunk import FilingChunk
from regradar.models.enums import EvalRunType, FilingStatus
from regradar.models.eval_run import EvalRun
from regradar.models.filing import Filing
from regradar.rag.answer_synthesis import PROMPT_VERSION, synthesize_answer
from regradar.rag.embeddings import _get_embedding_client
from regradar.rag.retriever import retrieve_similar_filings
from regradar.schemas.filings import SearchSource

logger = logging.getLogger(__name__)

# Matches migration 0010's seeded default organization — the harness has no
# organization-management concerns, same scope decision SEC-05 made for the
# CLI's create-api-key.
_DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

_RETRIEVAL_TOP_K = 5
_FIXTURE_SOURCE_DOCUMENT_PREFIX = "eval-01-fixture-"


@dataclass
class _CaseResult:
    faithfulness: float
    context_recall: float


def _current_git_commit_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[3],
            timeout=5,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        logger.warning("Could not resolve the current git commit SHA; leaving it null.")
        return None


async def _seed_fixture_corpus(db: AsyncSession) -> dict[uuid.UUID, str]:
    """Inserts SEED_FILINGS as real Filing/FilingChunk rows with real
    embeddings, so retrieval scores this exercises are real vector-search
    results, not stubbed ones. Returns {filing_id: entity_name}.

    Deliberately flushes, not commits: `set_rls_context`'s GUCs are set via
    `set_config(..., true)`, which is transaction-local — a commit here
    would end that transaction and silently revert the RLS role context
    before the scoring reads below ever run (real bug found in live
    verification: every case "retrieved no chunks" because the service-role
    context had already reverted). Seeding, scoring, and cleanup all share
    one transaction, rolled back at the end — the fixture rows are never
    committed at all, so no separate delete step is needed either.
    """
    client, model = _get_embedding_client()
    entity_names_by_filing_id: dict[uuid.UUID, str] = {}

    for index, seed in enumerate(SEED_FILINGS):
        filing_id = uuid.uuid4()
        entity_names_by_filing_id[filing_id] = seed.entity_name
        db.add(
            Filing(
                id=filing_id,
                organization_id=_DEFAULT_ORG_ID,
                source=seed.source,
                source_document_id=f"{_FIXTURE_SOURCE_DOCUMENT_PREFIX}{index}",
                entity_name=seed.entity_name,
                filing_type=seed.filing_type,
                filing_url="https://example.com/eval-01-fixture",
                published_at=datetime.now(UTC),
                ingested_at=datetime.now(UTC),
                status=FilingStatus.COMPLETE,
            )
        )
        embeddings = client.embeddings.create(model=model, input=seed.chunks).data
        for chunk_index, (chunk_text, embedding_data) in enumerate(zip(seed.chunks, embeddings, strict=True)):
            db.add(
                FilingChunk(
                    id=uuid.uuid4(),
                    filing_id=filing_id,
                    chunk_index=chunk_index,
                    chunk_text=chunk_text,
                    token_count=len(chunk_text.split()),
                    embedding=embedding_data.embedding,
                    is_table=False,
                )
            )

    await db.flush()
    return entity_names_by_filing_id


def _judge_llm():
    """The same model/provider every other LLM call in this project uses
    (tiered_router.select_model) — no separate judge-LLM credential."""
    choice = select_model(risk_level=None, task="analysis")
    client = AsyncOpenAI(base_url=choice.base_url, api_key=choice.api_key.get_secret_value())
    return llm_factory(choice.model, client=client)


async def _score_case(
    case: EvalCase,
    *,
    db: AsyncSession,
    entity_names_by_filing_id: dict[uuid.UUID, str],
    faithfulness_metric: Faithfulness,
    context_recall_metric: ContextRecall,
) -> _CaseResult | None:
    chunks = await retrieve_similar_filings(
        query_text=case.question,
        exclude_filing_id=uuid.uuid4(),
        db=db,
        top_k=_RETRIEVAL_TOP_K,
    )
    if not chunks:
        logger.warning("Eval case retrieved no chunks, skipping: %r", case.question)
        return None

    retrieved_contexts = [chunk.chunk_text for chunk in chunks]
    sources = [
        SearchSource(
            filing_id=chunk.filing_id,
            excerpt=chunk.chunk_text,
            entity_name=entity_names_by_filing_id.get(chunk.filing_id, "Unknown"),
        )
        for chunk in chunks
    ]

    answer = synthesize_answer(case.question, sources)
    if not answer:
        logger.warning("Eval case's answer synthesis failed, skipping: %r", case.question)
        return None

    faithfulness_result = await faithfulness_metric.ascore(
        user_input=case.question, response=answer, retrieved_contexts=retrieved_contexts
    )
    context_recall_result = await context_recall_metric.ascore(
        user_input=case.question, retrieved_contexts=retrieved_contexts, reference=case.reference
    )
    return _CaseResult(
        faithfulness=faithfulness_result.value, context_recall=context_recall_result.value
    )


async def run_eval(run_type: EvalRunType) -> EvalRun:
    """Runs the full EVAL-01 harness once and persists one `eval_runs` row.

    Raises RuntimeError if every fixture case failed (retrieval or
    synthesis) — writing a null/zero-scored row in that situation would be
    indistinguishable from a real, very-low-quality result, which is worse
    than a loud failure.
    """
    session_factory = get_session_factory()
    judge_llm = _judge_llm()
    faithfulness_metric = Faithfulness(llm=judge_llm)
    context_recall_metric = ContextRecall(llm=judge_llm)

    async with session_factory() as db:
        await set_rls_context(db, role="service")
        entity_names_by_filing_id = await _seed_fixture_corpus(db)
        try:
            results = [
                result
                for case in EVAL_CASES
                if (
                    result := await _score_case(
                        case,
                        db=db,
                        entity_names_by_filing_id=entity_names_by_filing_id,
                        faithfulness_metric=faithfulness_metric,
                        context_recall_metric=context_recall_metric,
                    )
                )
                is not None
            ]
        finally:
            # Never commits the seeded fixture rows in the first place (see
            # _seed_fixture_corpus's docstring) — rolling back here is what
            # actually discards them, not a delete against committed data.
            await db.rollback()

    if not results:
        raise RuntimeError(
            "Every eval case failed (empty retrieval or failed answer synthesis) — "
            "no eval_runs row was written. Check that Ollama/the configured LLM "
            "provider is reachable."
        )

    ragas_faithfulness = sum(r.faithfulness for r in results) / len(results)
    ragas_context_recall = sum(r.context_recall for r in results) / len(results)
    # METRIC_TARGETS' two ragas entries are always populated (unlike
    # hallucination_rate/avg_cost_per_filing_usd, which are genuinely None —
    # see its own definition in api/routers/metrics.py) — asserted, not
    # silently coerced, so a future edit there that nulls one out fails loud.
    faithfulness_target = METRIC_TARGETS["ragas_faithfulness"]
    context_recall_target = METRIC_TARGETS["ragas_context_recall"]
    assert faithfulness_target is not None
    assert context_recall_target is not None
    passed = ragas_faithfulness > faithfulness_target and ragas_context_recall > context_recall_target

    run = EvalRun(
        id=uuid.uuid4(),
        run_type=run_type,
        prompt_version=PROMPT_VERSION,
        git_commit_sha=_current_git_commit_sha(),
        ragas_faithfulness=ragas_faithfulness,
        ragas_context_recall=ragas_context_recall,
        passed=passed,
    )
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        db.add(run)
        await db.commit()

    logger.info(
        "EVAL-01 run complete: %d/%d cases scored, faithfulness=%.3f, context_recall=%.3f, passed=%s",
        len(results),
        len(EVAL_CASES),
        ragas_faithfulness,
        ragas_context_recall,
        passed,
    )
    return run
