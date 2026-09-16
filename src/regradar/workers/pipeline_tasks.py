"""Celery tasks that hand a stored filing off to the agent pipeline asynchronously.

`process_filing` builds the initial pipeline state from a stored filing
and runs it through the LangGraph supervisor graph (AGENT-01). This
module's job is the queue plumbing: reliable retry with backoff, and a
guarantee that a filing never silently disappears if the task ultimately
fails.
"""

import asyncio
import uuid

from celery import Task
from celery.utils.log import get_task_logger
from sqlalchemy import select

from regradar.agents.graph import build_graph
from regradar.agents.relevance_agent import compute_priority_score
from regradar.agents.state import OrgProfileSnapshot, PipelineState
from regradar.core.db import get_session_factory, set_rls_context
from regradar.models.brief import Brief
from regradar.models.enums import FilingStatus
from regradar.models.extraction import Extraction
from regradar.models.filing import Filing
from regradar.models.organization_profile import OrganizationProfile
from regradar.rag.chunking import chunk_filing
from regradar.rag.embeddings import embed_chunks
from regradar.rag.pdf_extraction import extract_text_and_tables, fetch_document_bytes
from regradar.workers.celery_app import celery_app

logger = get_task_logger(__name__)


async def _mark_filing_failed(filing_id: str, error_message: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        filing = await db.get(Filing, uuid.UUID(filing_id))
        if filing is None:
            logger.warning("Filing %s not found — nothing to mark failed", filing_id)
            return
        filing.status = FilingStatus.FAILED
        filing.processing_error = error_message
        await db.commit()


async def _run_pipeline_for_filing(filing_id: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        filing = await db.get(Filing, uuid.UUID(filing_id))
        if filing is None:
            logger.warning("Filing %s not found — skipping pipeline run", filing_id)
            return

        raw_text = ""
        chunks: list = []
        if filing.raw_pdf_s3_key:
            try:
                document_bytes = fetch_document_bytes(filing.raw_pdf_s3_key)
                raw_text, tables = extract_text_and_tables(document_bytes)
                if raw_text:
                    chunks = chunk_filing(raw_text, tables)
            except Exception as exc:  # noqa: BLE001 — never crash the pipeline over a bad/missing PDF
                logger.warning("PDF extraction failed for filing %s: %s", filing_id, exc)

        profile_row = await db.get(OrganizationProfile, filing.organization_id)
        org_profile = (
            OrgProfileSnapshot(
                industry=profile_row.industry,
                business_description=profile_row.business_description,
                watchlist_entities=list(profile_row.watchlist_entities),
                products=list(profile_row.products),
                risk_priorities=list(profile_row.risk_priorities),
            )
            if profile_row is not None
            else None
        )

        state = PipelineState(
            filing_id=filing.id, raw_text=raw_text, chunks=chunks or None, org_profile=org_profile
        )
        result = await build_graph().ainvoke(state, config={"configurable": {"db": db}})

        # Real bug found live (first time this pipeline ever ran against a
        # populated deliveries table with an actual channel configured):
        # deliver_node's own _record_delivery commits per-channel (see its
        # docstring) — set_config(..., true) is transaction-scoped, so each
        # of those commits ends the transaction this function's own
        # set_rls_context call above was scoped to, silently reverting
        # app.current_role. Every write below this point must re-assert it
        # first, or its RLS-gated UPDATE/INSERT is silently filtered to
        # zero rows (raises StaleDataError for the UPDATE case) instead of
        # actually being denied loudly.
        await set_rls_context(db, role="service")

        # A prior failed attempt's error must not linger once a new run
        # actually reaches this point — reprocessing (e.g. via
        # process-pending or the Filings page's "Process now") always
        # supersedes whatever _mark_filing_failed recorded last time.
        filing.processing_error = None

        if result["domain"] is None:
            filing.status = FilingStatus.NEEDS_CLASSIFICATION
        else:
            filing.domain = result["domain"]
            filing.risk_level = result["risk_level"]
            filing.classification_confidence = result["classification_confidence"]
            relevance_result = result.get("relevance")
            if relevance_result is not None:
                filing.priority_score = compute_priority_score(
                    result["risk_level"], relevance_result.relevance_score
                )
                filing.relevance_rationale = relevance_result.rationale
                filing.recommended_action = relevance_result.recommended_action
                filing.matched_signals = relevance_result.matched_signals
            extraction_missing = result["extraction"] is None and chunks
            briefs_missing = result["extraction"] is not None and result["briefs"] is None
            if extraction_missing or briefs_missing:
                filing.status = FilingStatus.NEEDS_REVIEW
            elif result["delivery_status"] is not None and result["delivery_success"]:
                filing.status = FilingStatus.COMPLETE
            else:
                filing.status = FilingStatus.CLASSIFYING
        await db.commit()

        if result["extraction"] is not None:
            # result["extraction"] is a real ExtractionResult instance —
            # ainvoke() does not flatten nested Pydantic sub-models into
            # plain dicts (verified) — so this uses attribute access and
            # model_dump(), never dict-subscript access.
            extraction_result = result["extraction"]
            retrieved_chunks = result.get("retrieved_chunks") or []
            # Distinct filing_ids, order preserved — this is the only place
            # AGENT-06's retrieval results are ever persisted; PipelineState
            # itself only lives for the duration of one pipeline run.
            similar_filing_ids = list(
                dict.fromkeys(str(chunk.filing_id) for chunk in retrieved_chunks)
            )
            await set_rls_context(db, role="service")  # see the comment above — re-assert post-commit
            db.add(
                Extraction(
                    filing_id=filing.id,
                    obligations=extraction_result.obligations,
                    deadlines=extraction_result.deadlines,
                    risk_flags=extraction_result.risk_flags,
                    affected_products=extraction_result.affected_products,
                    key_entities=extraction_result.key_entities,
                    competitor_mentions=extraction_result.competitor_mentions,
                    model_used=extraction_result.model_used,
                    raw_model_response=extraction_result.model_dump(),
                    similar_filing_ids=similar_filing_ids or None,
                )
            )
            await db.commit()

        if result["briefs"] is not None:
            # result["briefs"] is a real BriefSet instance — same
            # ainvoke() nested-Pydantic-model behavior verified for
            # ExtractionResult in AGENT-07 — attribute access only.
            briefs_result = result["briefs"]
            try:
                await set_rls_context(db, role="service")  # see the comment above — re-assert post-commit
                db.add(
                    Brief(
                        filing_id=filing.id,
                        executive_brief=briefs_result.executive_brief,
                        cco_summary=briefs_result.cco_summary,
                        analyst_summary=briefs_result.analyst_summary,
                        engineer_summary=briefs_result.engineer_summary,
                        model_used=briefs_result.model_used,
                    )
                )
                await db.commit()
            except Exception as exc:  # noqa: BLE001 — a transient Brief-insert failure must
                # not re-trigger the whole task (with autoretry_for=(Exception,)) and re-run
                # already-committed classification/extraction, which would hit the
                # Extraction.filing_id unique constraint on retry. Degrade gracefully instead —
                # filing.status was already set/committed above this block, so it's untouched.
                logger.warning("Brief persistence failed for filing %s: %s", filing_id, exc)

        if raw_text:
            try:
                await set_rls_context(db, role="service")  # see the comment above — re-assert post-commit
                await embed_chunks(filing.id, chunks, db)
            except Exception as exc:  # noqa: BLE001 — a transient embedding failure must not
                # re-trigger the whole task (with autoretry_for=(Exception,)) and re-run
                # already-committed classification/extraction, which would hit the
                # Extraction.filing_id unique constraint on retry. Embeddings are only
                # needed for future filings' retrieval, so degrade gracefully instead.
                logger.warning("Embedding failed for filing %s: %s", filing_id, exc)


class _ProcessFilingTask(Task):
    """Marks the filing status=failed once retries are exhausted.

    on_failure fires when the task's final attempt still raises — i.e.
    after autoretry_for has already retried up to max_retries. It never
    fires for an attempt that's merely being retried, only for the
    genuinely final failure, so a filing is only marked failed once the
    pipeline has truly given up on it.
    """

    def on_failure(self, exc: BaseException, task_id: str, args: tuple, kwargs: dict, einfo) -> None:
        filing_id = args[0] if args else kwargs.get("filing_id")
        if filing_id is None:
            logger.error("process_filing failed with no filing_id in args/kwargs: %s", exc)
            return
        logger.error("process_filing exhausted retries for filing %s: %s", filing_id, exc)
        asyncio.run(_mark_filing_failed(str(filing_id), str(exc)))


@celery_app.task(
    base=_ProcessFilingTask,
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def process_filing(self: Task, filing_id: str) -> None:
    """Run the agent pipeline for one filing."""
    asyncio.run(_run_pipeline_for_filing(filing_id))


def enqueue_filing_processing(filing_id: uuid.UUID) -> None:
    """Enqueue a filing for pipeline processing.

    The only Celery-aware function ingestion code should ever call —
    keeps task names, apply_async/delay, and other Celery specifics out
    of ingestion/ entirely.
    """
    process_filing.delay(str(filing_id))


async def process_pending_filings() -> list[tuple[uuid.UUID, bool]]:
    """Run the pipeline for every filing still at status=ingested, one at a
    time, synchronously — no Celery worker or broker required.

    This is the on-demand, cost-gated counterpart to `poll-once`:
    ingestion is free (metadata only), this is what actually spends LLM
    tokens (classification, extraction, summarization) and triggers
    delivery — so it is a deliberate, separate command rather than
    something ingestion ever triggers automatically. Ingestion code never
    calls this or `enqueue_filing_processing` itself, by design: the two
    stages (fetch vs. spend-and-deliver) are decided independently.

    Returns a list of (filing_id, succeeded) pairs. One filing's failure
    is caught and marked (via _mark_filing_failed) rather than stopping
    the batch — this bypasses process_filing's Celery retry machinery
    entirely, so a genuine transient failure here is not retried; re-run
    `process-pending` to pick it back up (it's still at status=ingested
    only if _run_pipeline_for_filing raised before committing any status
    change, otherwise it's `failed` and won't be picked up again).
    """
    session_factory = get_session_factory()
    async with session_factory() as db:
        await set_rls_context(db, role="service")
        result = await db.execute(select(Filing.id).where(Filing.status == FilingStatus.INGESTED))
        pending_ids = list(result.scalars().all())

    results: list[tuple[uuid.UUID, bool]] = []
    for filing_id in pending_ids:
        try:
            await _run_pipeline_for_filing(str(filing_id))
            results.append((filing_id, True))
        except Exception as exc:  # noqa: BLE001 — one filing's failure must not stop the batch
            logger.error("process-pending: filing %s failed: %s", filing_id, exc)
            await _mark_filing_failed(str(filing_id), str(exc))
            results.append((filing_id, False))
    return results
