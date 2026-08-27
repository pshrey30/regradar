"""EVAL-01/EVAL-02/EVAL-03/EVAL-04 — the eval harness.

One `regradar run-eval` invocation runs whichever fixture eval sets are
built so far and writes a single averaged `eval_runs` row with whatever it
managed to measure — never multiple partial rows per run, so `GET
/v1/metrics`' "latest row" always reflects everything currently measurable
in one place, not just whichever harness ran most recently.

EVAL-01 (ragas_faithfulness/ragas_context_recall): seeds a small,
self-contained fixture corpus (evaluation/dataset.py) into
`filings`/`filing_chunks` under the default organization, runs each fixture
question through the *real* API-06 retrieval + answer-synthesis pipeline
(rag.retriever.retrieve_similar_filings, rag.answer_synthesis.synthesize_answer
— exactly what POST /v1/filings/search calls), scores each (question,
retrieved context, generated answer, reference answer) tuple with real Ragas
Faithfulness/ContextRecall metrics, and always rolls back its fixture
inserts afterward (never committed at all) — a run never leaves synthetic
filings visible to a real API caller. The judge LLM is the same
local-Ollama-or-real-OpenAI choice every other LLM call in this project
makes (llm_routing.tiered_router.select_model) — no new provider, no new
credential, consistent with ADR-05.

EVAL-02 (rouge_l): runs a small, hand-written extraction-shaped fixture set
through the *real* Summarization Agent (agents.summarization_agent.
summarize_node — exactly what the real pipeline calls) and scores each
generated executive_brief against a reference via ROUGE-L
(rouge_score.rouge_scorer, entirely local — no network fetch, unlike
`evaluate.load("rouge")`). No DB writes of its own — summarize_node is pure.

EVAL-03 (alert_precision/alert_recall): runs a small, hand-written fixture
set of filing texts through the *real* Triage Agent (agents.triage_agent.
triage_node — a real HF zero-shot classification call, plus a real
dual-model spot-check for low-confidence cases), scores each as a binary
alert/no-alert classifier (risk_level HIGH or CRITICAL — the same threshold
delivery_agent.py's filter_min_risk check uses to gate webhook/Slack/email
delivery) against a hand-labeled `expected_alert`, and aggregates the whole
set's true/false positives/negatives into one precision and one recall
figure — these two are computed over the fixture set as a whole, unlike
ragas/rouge_l's per-case average, since that's what precision/recall mean.

EVAL-04 (extraction_f1): runs a small, hand-written fixture set of filing
chunks through the *real* Analysis Agent (agents.analysis_agent.
analyze_node — a real structured-extraction LLM call). ExtractionResult has
six fields (obligations, deadlines, risk_flags, affected_products,
key_entities, competitor_mentions) but eval_runs has one extraction_f1
column, so each case's six fields are flattened into one bag of extracted
strings and greedily fuzzy-matched (token-Jaccard similarity, no exact-text
requirement — an LLM's exact phrasing never matches hand-written ground
truth verbatim) against a hand-labeled `expected_items` list, scored as one
F1 per case. Per-case F1s are averaged across the fixture set, the same way
EVAL-02's rouge_l is — unlike EVAL-03's alert precision/recall, which pools
raw counts across the whole set instead, because each extraction case has
its own independent ground-truth set rather than one shared confusion
matrix.

Each metric family scores independently: if one family's every case fails,
its eval_runs column(s) stay null (not silently coerced to 0) and the run
still writes whatever the other families measured. Only raises RuntimeError
if NOTHING was measured across every family — that's the one case where
writing a row would be pointless.

hallucination_rate/avg_cost_per_filing_usd/p99_latency_ms are EVAL-06's own
scope, deliberately left null (not zero — GET /v1/metrics distinguishes
"not measured" from "measured as zero" via `MetricValue.value` being
nullable).
"""

import logging
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from openai import AsyncOpenAI
from ragas.llms import llm_factory
from ragas.metrics.collections import ContextRecall, Faithfulness
from rouge_score import rouge_scorer
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.agents.analysis_agent import analyze_node
from regradar.agents.state import ExtractionResult, PipelineState
from regradar.agents.summarization_agent import summarize_node
from regradar.agents.triage_agent import triage_node
from regradar.api.routers.metrics import METRIC_TARGETS
from regradar.core.db import get_session_factory, set_rls_context
from regradar.evaluation.dataset import (
    ALERT_EVAL_CASES,
    EXTRACTION_EVAL_CASES,
    SEARCH_EVAL_CASES,
    SEED_FILINGS,
    SUMMARIZATION_EVAL_CASES,
    AlertEvalCase,
    ExtractionEvalCase,
    SearchEvalCase,
    SummarizationEvalCase,
)
from regradar.llm_routing.tiered_router import select_model
from regradar.models.chunk import FilingChunk
from regradar.models.enums import EvalRunType, FilingStatus, RiskLevel
from regradar.models.eval_run import EvalRun
from regradar.models.filing import Filing
from regradar.rag.answer_synthesis import PROMPT_VERSION, synthesize_answer
from regradar.rag.chunking import Chunk
from regradar.rag.embeddings import _get_embedding_client
from regradar.rag.retriever import retrieve_similar_filings
from regradar.schemas.filings import SearchSource

logger = logging.getLogger(__name__)

_FUZZY_MATCH_THRESHOLD = 0.5

_ALERT_RISK_LEVELS = {RiskLevel.HIGH, RiskLevel.CRITICAL}

# Matches migration 0010's seeded default organization — the harness has no
# organization-management concerns, same scope decision SEC-05 made for the
# CLI's create-api-key.
_DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

_RETRIEVAL_TOP_K = 5
_FIXTURE_SOURCE_DOCUMENT_PREFIX = "eval-01-fixture-"


@dataclass
class _SearchCaseResult:
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


async def _score_search_case(
    case: SearchEvalCase,
    *,
    db: AsyncSession,
    entity_names_by_filing_id: dict[uuid.UUID, str],
    faithfulness_metric: Faithfulness,
    context_recall_metric: ContextRecall,
) -> _SearchCaseResult | None:
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
    return _SearchCaseResult(
        faithfulness=faithfulness_result.value, context_recall=context_recall_result.value
    )


def _score_summarization_case(case: SummarizationEvalCase) -> float | None:
    """Runs the real summarize_node against one fixture case and scores its
    generated executive_brief against the reference via ROUGE-L. Returns
    None (never raises) if summarization itself failed — summarize_node's
    own contract is to degrade to `briefs=None` on failure, never raise."""
    state = PipelineState(
        filing_id=uuid.uuid4(),
        raw_text="",
        domain=case.domain,
        risk_level=case.risk_level,
        extraction=ExtractionResult(
            obligations=case.obligations,
            deadlines=case.deadlines,
            risk_flags=case.risk_flags,
            affected_products=case.affected_products,
            key_entities=case.key_entities,
        ),
    )
    result_state = summarize_node(state)
    if result_state.briefs is None:
        logger.warning(
            "Summarization eval case failed to produce a brief, skipping: entities=%r",
            case.key_entities,
        )
        return None

    scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)
    score = scorer.score(case.reference_executive_brief, result_state.briefs.executive_brief)
    return score["rougeL"].fmeasure


@dataclass
class _AlertCaseResult:
    expected_alert: bool
    predicted_alert: bool


def _score_alert_case(case: AlertEvalCase) -> _AlertCaseResult | None:
    """Runs the real triage_node against one fixture case and derives its
    binary alert/no-alert prediction from the resulting risk_level. Returns
    None (never raises) if triage itself failed to classify — triage_node's
    own contract is to leave risk_level=None on a real classification
    failure (e.g. the HF endpoint being unreachable after retry), never
    raise; that's a harness/infra problem, not a wrong prediction, so it's
    excluded from the confusion matrix rather than counted as either."""
    state = PipelineState(filing_id=uuid.uuid4(), raw_text=case.raw_text)
    result_state = triage_node(state)
    if result_state.risk_level is None:
        logger.warning(
            "Alert eval case failed to classify, skipping: %r", case.raw_text[:80]
        )
        return None

    return _AlertCaseResult(
        expected_alert=case.expected_alert,
        predicted_alert=result_state.risk_level in _ALERT_RISK_LEVELS,
    )


def _precision_recall(results: list[_AlertCaseResult]) -> tuple[float | None, float | None]:
    """Aggregate precision/recall over the whole fixture set's confusion
    matrix — not a per-case average, since that isn't what these metrics
    mean. Either figure is None (not 0.0) when its denominator is zero, so
    an all-negative or all-predicted-negative fixture set doesn't silently
    report a fabricated perfect or zero score."""
    true_positives = sum(1 for r in results if r.expected_alert and r.predicted_alert)
    false_positives = sum(1 for r in results if not r.expected_alert and r.predicted_alert)
    false_negatives = sum(1 for r in results if r.expected_alert and not r.predicted_alert)

    predicted_positives = true_positives + false_positives
    actual_positives = true_positives + false_negatives
    precision = true_positives / predicted_positives if predicted_positives > 0 else None
    recall = true_positives / actual_positives if actual_positives > 0 else None
    return precision, recall


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _token_jaccard(a: str, b: str) -> float:
    tokens_a, tokens_b = _tokenize(a), _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _extraction_f1(predicted: list[str], expected: list[str]) -> float:
    """Greedily matches each predicted item against its best-scoring unused
    expected item (token-Jaccard similarity >= _FUZZY_MATCH_THRESHOLD), the
    same "loose" methodology real entity-extraction eval uses — an LLM's
    exact phrasing never matches hand-written ground truth verbatim, so an
    exact-string match would always undercount. A case with nothing
    expected and nothing predicted is a trivial perfect match (1.0); a case
    where exactly one side is empty is a trivial total miss (0.0)."""
    if not predicted and not expected:
        return 1.0
    if not predicted or not expected:
        return 0.0

    remaining_expected = list(expected)
    true_positives = 0
    for item in predicted:
        if not remaining_expected:
            break
        best_index, best_score = None, 0.0
        for index, candidate in enumerate(remaining_expected):
            score = _token_jaccard(item, candidate)
            if score > best_score:
                best_index, best_score = index, score
        if best_index is not None and best_score >= _FUZZY_MATCH_THRESHOLD:
            true_positives += 1
            remaining_expected.pop(best_index)

    precision = true_positives / len(predicted)
    recall = true_positives / len(expected)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _score_extraction_case(case: ExtractionEvalCase) -> float | None:
    """Runs the real analyze_node against one fixture case and scores its
    flattened ExtractionResult against expected_items via F1. Returns None
    (never raises) if extraction itself failed — analyze_node's own
    contract is to degrade to `extraction=None` on failure, never raise."""
    state = PipelineState(
        filing_id=uuid.uuid4(),
        raw_text="",
        chunks=[
            Chunk(
                chunk_index=0,
                chunk_text=case.chunk_text,
                section_reference=None,
                token_count=len(case.chunk_text.split()),
                is_table=False,
            )
        ],
    )
    result_state = analyze_node(state)
    if result_state.extraction is None:
        logger.warning(
            "Extraction eval case failed to produce a result, skipping: %r",
            case.chunk_text[:80],
        )
        return None

    extraction = result_state.extraction
    predicted_items = (
        [o.get("description", "") for o in extraction.obligations]
        + [d.get("description", "") for d in extraction.deadlines]
        + extraction.risk_flags
        + extraction.affected_products
        + extraction.key_entities
        + extraction.competitor_mentions
    )
    return _extraction_f1(predicted_items, case.expected_items)


def _target(field: str) -> float:
    """METRIC_TARGETS' entries for every metric this harness ever measures
    are non-null by definition (unlike hallucination_rate/
    avg_cost_per_filing_usd, which are genuinely None — see its own
    definition in api/routers/metrics.py) — asserted, not silently
    coerced, so a future edit there that nulls one out fails loud."""
    target = METRIC_TARGETS[field]
    assert target is not None, f"METRIC_TARGETS[{field!r}] must be non-null for a harness that measures it"
    return target


async def _run_search_eval(session_factory) -> list[_SearchCaseResult]:
    judge_llm = _judge_llm()
    faithfulness_metric = Faithfulness(llm=judge_llm)
    context_recall_metric = ContextRecall(llm=judge_llm)

    async with session_factory() as db:
        await set_rls_context(db, role="service")
        entity_names_by_filing_id = await _seed_fixture_corpus(db)
        try:
            return [
                result
                for case in SEARCH_EVAL_CASES
                if (
                    result := await _score_search_case(
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


async def run_eval(run_type: EvalRunType) -> EvalRun:
    """Runs every fixture eval set built so far and persists one `eval_runs`
    row with whatever it measured.

    Each metric family (EVAL-01's ragas pair, EVAL-02's rouge_l, EVAL-03's
    alert precision/recall, EVAL-04's extraction_f1) scores independently —
    one family failing entirely leaves its column(s) null, not zero, and
    doesn't stop the other families' columns from being written. Raises
    RuntimeError only if NOTHING was measured across every family — a
    completely empty row would be pointless to write, and most likely means
    an LLM/classification provider is unreachable.
    """
    session_factory = get_session_factory()

    search_results = await _run_search_eval(session_factory)
    summarization_scores = [
        score
        for case in SUMMARIZATION_EVAL_CASES
        if (score := _score_summarization_case(case)) is not None
    ]
    alert_results = [
        result
        for case in ALERT_EVAL_CASES
        if (result := _score_alert_case(case)) is not None
    ]
    extraction_scores = [
        score
        for case in EXTRACTION_EVAL_CASES
        if (score := _score_extraction_case(case)) is not None
    ]

    if (
        not search_results
        and not summarization_scores
        and not alert_results
        and not extraction_scores
    ):
        raise RuntimeError(
            "Every eval case failed across every metric family — no eval_runs row was "
            "written. Check that Ollama/HuggingFace/the configured LLM provider is reachable."
        )

    measured: dict[str, float] = {}
    if search_results:
        measured["ragas_faithfulness"] = sum(r.faithfulness for r in search_results) / len(
            search_results
        )
        measured["ragas_context_recall"] = sum(r.context_recall for r in search_results) / len(
            search_results
        )
    else:
        logger.warning("Every search eval case failed; ragas_faithfulness/context_recall stay null.")

    if summarization_scores:
        measured["rouge_l"] = sum(summarization_scores) / len(summarization_scores)
    else:
        logger.warning("Every summarization eval case failed; rouge_l stays null.")

    if alert_results:
        alert_precision, alert_recall = _precision_recall(alert_results)
        if alert_precision is not None:
            measured["alert_precision"] = alert_precision
        if alert_recall is not None:
            measured["alert_recall"] = alert_recall
        if alert_precision is None and alert_recall is None:
            logger.warning(
                "Alert eval cases scored but neither precision nor recall had a "
                "non-zero denominator; alert_precision/alert_recall stay null."
            )
    else:
        logger.warning("Every alert eval case failed; alert_precision/alert_recall stay null.")

    if extraction_scores:
        measured["extraction_f1"] = sum(extraction_scores) / len(extraction_scores)
    else:
        logger.warning("Every extraction eval case failed; extraction_f1 stays null.")

    passed = all(value > _target(field) for field, value in measured.items())

    run = EvalRun(
        id=uuid.uuid4(),
        run_type=run_type,
        prompt_version=PROMPT_VERSION,
        git_commit_sha=_current_git_commit_sha(),
        ragas_faithfulness=measured.get("ragas_faithfulness"),
        ragas_context_recall=measured.get("ragas_context_recall"),
        rouge_l=measured.get("rouge_l"),
        alert_precision=measured.get("alert_precision"),
        alert_recall=measured.get("alert_recall"),
        extraction_f1=measured.get("extraction_f1"),
        passed=passed,
    )
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        db.add(run)
        await db.commit()

    logger.info(
        "Eval run complete: %d/%d search cases, %d/%d summarization cases, %d/%d alert "
        "cases, %d/%d extraction cases scored, measured=%s, passed=%s",
        len(search_results),
        len(SEARCH_EVAL_CASES),
        len(summarization_scores),
        len(SUMMARIZATION_EVAL_CASES),
        len(alert_results),
        len(ALERT_EVAL_CASES),
        len(extraction_scores),
        len(EXTRACTION_EVAL_CASES),
        measured,
        passed,
    )
    return run
