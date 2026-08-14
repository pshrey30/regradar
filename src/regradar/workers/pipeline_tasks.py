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

from regradar.agents.graph import build_graph
from regradar.agents.state import PipelineState
from regradar.core.db import get_session_factory
from regradar.models.enums import FilingStatus
from regradar.models.filing import Filing
from regradar.rag.chunking import chunk_filing
from regradar.rag.embeddings import embed_chunks
from regradar.rag.pdf_extraction import extract_text_and_tables, fetch_pdf_bytes
from regradar.workers.celery_app import celery_app

logger = get_task_logger(__name__)


async def _mark_filing_failed(filing_id: str, error_message: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as db:
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
        filing = await db.get(Filing, uuid.UUID(filing_id))
        if filing is None:
            logger.warning("Filing %s not found — skipping pipeline run", filing_id)
            return

        raw_text = ""
        tables: list = []
        if filing.raw_pdf_s3_key:
            try:
                pdf_bytes = fetch_pdf_bytes(filing.raw_pdf_s3_key)
                raw_text, tables = extract_text_and_tables(pdf_bytes)
            except Exception as exc:  # noqa: BLE001 — never crash the pipeline over a bad/missing PDF
                logger.warning("PDF extraction failed for filing %s: %s", filing_id, exc)

        state = PipelineState(filing_id=filing.id, raw_text=raw_text)
        result = build_graph().invoke(state)

        if result["domain"] is None:
            filing.status = FilingStatus.NEEDS_CLASSIFICATION
        else:
            filing.domain = result["domain"]
            filing.risk_level = result["risk_level"]
            filing.classification_confidence = result["classification_confidence"]
            filing.status = FilingStatus.CLASSIFYING
        await db.commit()

        if raw_text:
            chunks = chunk_filing(raw_text, tables)
            await embed_chunks(filing.id, chunks, db)


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
