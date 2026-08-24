"""Unit tests for the Celery pipeline task queue wiring.

celery_app.py resolves Settings eagerly at import time (see its module
docstring for why) — that means required env vars must be set *before*
this module is imported, not via a pytest fixture, since imports happen
at collection time, before any fixture runs.
"""

import os

os.environ.setdefault("APP_SECRET_KEY", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_BUCKET_NAME", "test-bucket")
os.environ.setdefault("S3_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("HUGGINGFACE_API_TOKEN", "test")
os.environ.setdefault("SEC_EDGAR_USER_AGENT", "RegRadar/1.0 (test@example.com)")

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from regradar.agents.state import BriefSet, ExtractionResult
from regradar.models.brief import Brief
from regradar.models.enums import FilingDomain, FilingStatus, RiskLevel
from regradar.models.extraction import Extraction
from regradar.models.filing import Filing
from regradar.rag.chunking import Chunk
from regradar.workers.pipeline_tasks import (
    _mark_filing_failed,
    _ProcessFilingTask,
    enqueue_filing_processing,
    process_filing,
)


def test_enqueue_filing_processing_calls_delay_with_str_id() -> None:
    filing_id = uuid.uuid4()
    with patch.object(process_filing, "delay") as mock_delay:
        enqueue_filing_processing(filing_id)
        mock_delay.assert_called_once_with(str(filing_id))


def test_process_filing_persists_classification_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.FINANCIAL,
                    "risk_level": RiskLevel.LOW,
                    "classification_confidence": 0.9,
                    "extraction": None,
                    "briefs": None,
                    "delivery_status": None,
                    "delivery_success": None,
                }
            )
        ),
    )

    process_filing.run(str(filing_id))

    mock_db.get.assert_awaited_once_with(Filing, filing_id)
    assert filing.domain == FilingDomain.FINANCIAL
    assert filing.risk_level == RiskLevel.LOW
    assert filing.classification_confidence == 0.9
    assert filing.status == FilingStatus.CLASSIFYING
    mock_db.commit.assert_awaited_once()


def test_process_filing_marks_needs_classification_when_triage_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": None,
                    "risk_level": None,
                    "classification_confidence": None,
                    "extraction": None,
                    "briefs": None,
                    "delivery_status": None,
                    "delivery_success": None,
                }
            )
        ),
    )

    process_filing.run(str(filing_id))

    assert filing.status == FilingStatus.NEEDS_CLASSIFICATION
    mock_db.commit.assert_awaited_once()


def test_process_filing_extracts_text_and_embeds_chunks_when_pdf_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = "filings/abc123.pdf"

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.FINANCIAL,
                    "risk_level": RiskLevel.LOW,
                    "classification_confidence": 0.9,
                    "extraction": None,
                    "briefs": None,
                    "delivery_status": None,
                    "delivery_success": None,
                }
            )
        ),
    )
    monkeypatch.setattr(
        pipeline_tasks_module, "fetch_document_bytes", lambda s3_key: b"fake pdf bytes"
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "extract_text_and_tables",
        lambda pdf_bytes: ("Item 1. Real extracted filing text.", []),
    )
    fake_chunks = [
        Chunk(
            chunk_index=0,
            chunk_text="Item 1. Real extracted filing text.",
            section_reference="Item 1.",
            token_count=6,
            is_table=False,
        )
    ]
    monkeypatch.setattr(
        pipeline_tasks_module, "chunk_filing", lambda text, tables: fake_chunks
    )
    mock_embed_chunks = AsyncMock()
    monkeypatch.setattr(pipeline_tasks_module, "embed_chunks", mock_embed_chunks)

    process_filing.run(str(filing_id))

    mock_embed_chunks.assert_awaited_once_with(filing_id, fake_chunks, mock_db)


def test_process_filing_falls_back_to_empty_text_when_pdf_extraction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = "filings/abc123.pdf"

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )

    captured_state = {}

    async def _fake_ainvoke(state, config=None):
        captured_state["raw_text"] = state.raw_text
        return {
            "domain": FilingDomain.FINANCIAL,
            "risk_level": RiskLevel.LOW,
            "classification_confidence": 0.9,
            "extraction": None,
            "briefs": None,
            "delivery_status": None,
            "delivery_success": None,
        }

    monkeypatch.setattr(
        pipeline_tasks_module, "build_graph", lambda: MagicMock(ainvoke=_fake_ainvoke)
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "fetch_document_bytes",
        MagicMock(side_effect=RuntimeError("S3 unavailable")),
    )
    mock_embed_chunks = AsyncMock()
    monkeypatch.setattr(pipeline_tasks_module, "embed_chunks", mock_embed_chunks)

    process_filing.run(str(filing_id))

    assert captured_state["raw_text"] == ""
    mock_embed_chunks.assert_not_awaited()


def test_process_filing_skips_extraction_when_no_pdf_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.FINANCIAL,
                    "risk_level": RiskLevel.LOW,
                    "classification_confidence": 0.9,
                    "extraction": None,
                    "briefs": None,
                    "delivery_status": None,
                    "delivery_success": None,
                }
            )
        ),
    )
    mock_fetch = MagicMock()
    monkeypatch.setattr(pipeline_tasks_module, "fetch_document_bytes", mock_fetch)
    mock_embed_chunks = AsyncMock()
    monkeypatch.setattr(pipeline_tasks_module, "embed_chunks", mock_embed_chunks)

    process_filing.run(str(filing_id))

    mock_fetch.assert_not_called()
    mock_embed_chunks.assert_not_awaited()


def test_process_filing_persists_extraction_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    # ainvoke() returns nested Pydantic sub-models as real instances, not
    # dicts — verified against a real LangGraph call — so the mock here
    # must match: a real ExtractionResult, not a plain dict.
    extraction_result = ExtractionResult(
        obligations=[{"description": "File report.", "source_chunk_index": 0}],
        deadlines=[{"description": "Annual report", "date": "2027-01-01"}],
        risk_flags=["material weakness"],
        affected_products=[],
        key_entities=["Acme Corp"],
        competitor_mentions=[],
        model_used="llama3.1",
    )
    briefs_result = BriefSet(
        executive_brief="Sentence one. Sentence two. Sentence three.",
        cco_summary="Short board-level summary.",
        analyst_summary="- File report by 2027-01-01",
        engineer_summary=f"filing_id={filing_id} domain=financial risk_level=low obligations_extracted=1 status=processed",
        model_used="llama3.1",
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.FINANCIAL,
                    "risk_level": RiskLevel.LOW,
                    "classification_confidence": 0.9,
                    "extraction": extraction_result,
                    "briefs": briefs_result,
                    "delivery_status": None,
                    "delivery_success": None,
                }
            )
        ),
    )

    process_filing.run(str(filing_id))

    assert mock_db.add.call_count == 2
    added_extraction = mock_db.add.call_args_list[0].args[0]
    assert isinstance(added_extraction, Extraction)
    assert added_extraction.filing_id == filing_id
    assert added_extraction.obligations == extraction_result.obligations
    assert added_extraction.model_used == "llama3.1"
    assert filing.status == FilingStatus.CLASSIFYING


def test_process_filing_marks_needs_review_when_extraction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = "filings/abc123.pdf"

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    monkeypatch.setattr(
        pipeline_tasks_module, "fetch_document_bytes", lambda s3_key: b"fake pdf bytes"
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "extract_text_and_tables",
        lambda pdf_bytes: ("Item 1. Real extracted filing text.", []),
    )
    fake_chunks = [
        Chunk(
            chunk_index=0,
            chunk_text="Item 1. Real extracted filing text.",
            section_reference="Item 1.",
            token_count=6,
            is_table=False,
        )
    ]
    monkeypatch.setattr(
        pipeline_tasks_module, "chunk_filing", lambda text, tables: fake_chunks
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.FINANCIAL,
                    "risk_level": RiskLevel.LOW,
                    "classification_confidence": 0.9,
                    "extraction": None,
                    "briefs": None,
                    "delivery_status": None,
                    "delivery_success": None,
                }
            )
        ),
    )
    mock_embed_chunks = AsyncMock()
    monkeypatch.setattr(pipeline_tasks_module, "embed_chunks", mock_embed_chunks)

    process_filing.run(str(filing_id))

    mock_db.add.assert_not_called()
    assert filing.status == FilingStatus.NEEDS_REVIEW
    # Triage succeeded even though extraction failed — that result must not
    # be discarded just because a separate concern (extraction) failed.
    assert filing.domain == FilingDomain.FINANCIAL
    assert filing.risk_level == RiskLevel.LOW
    assert filing.classification_confidence == 0.9


def test_process_filing_marks_complete_when_delivery_ran(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    extraction_result = ExtractionResult(
        obligations=[{"description": "File report.", "source_chunk_index": 0}],
        deadlines=[{"description": "Annual report", "date": "2027-01-01"}],
        risk_flags=["material weakness"],
        affected_products=[],
        key_entities=["Acme Corp"],
        competitor_mentions=[],
        model_used="llama3.1",
    )
    briefs_result = BriefSet(
        executive_brief="Sentence one. Sentence two. Sentence three.",
        cco_summary="Short board-level summary.",
        analyst_summary="- File report by 2027-01-01",
        engineer_summary=f"filing_id={filing_id} domain=financial risk_level=low obligations_extracted=1 status=processed",
        model_used="llama3.1",
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.FINANCIAL,
                    "risk_level": RiskLevel.LOW,
                    "classification_confidence": 0.9,
                    "extraction": extraction_result,
                    "briefs": briefs_result,
                    "delivery_status": "slack=sent",
                    "delivery_success": True,
                }
            )
        ),
    )

    process_filing.run(str(filing_id))

    assert mock_db.add.call_count == 2
    added_objects = [call.args[0] for call in mock_db.add.call_args_list]
    assert isinstance(added_objects[0], Extraction)
    added_brief = added_objects[1]
    assert isinstance(added_brief, Brief)
    assert added_brief.filing_id == filing_id
    assert added_brief.executive_brief == briefs_result.executive_brief
    assert added_brief.cco_summary == briefs_result.cco_summary
    assert added_brief.analyst_summary == briefs_result.analyst_summary
    assert added_brief.engineer_summary == briefs_result.engineer_summary
    assert added_brief.model_used == "llama3.1"
    assert filing.status == FilingStatus.COMPLETE


def test_process_filing_stays_classifying_when_delivery_ran_but_nothing_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delivery_status non-None but delivery_success False (every configured
    channel failed) must NOT be treated as COMPLETE — it must fall through
    to the CLASSIFYING default, same as if delivery hadn't run at all."""
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    extraction_result = ExtractionResult(
        obligations=[{"description": "File report.", "source_chunk_index": 0}],
        deadlines=[{"description": "Annual report", "date": "2027-01-01"}],
        risk_flags=["material weakness"],
        affected_products=[],
        key_entities=["Acme Corp"],
        competitor_mentions=[],
        model_used="llama3.1",
    )
    briefs_result = BriefSet(
        executive_brief="Sentence one. Sentence two. Sentence three.",
        cco_summary="Short board-level summary.",
        analyst_summary="- File report by 2027-01-01",
        engineer_summary=f"filing_id={filing_id} domain=financial risk_level=low obligations_extracted=1 status=processed",
        model_used="llama3.1",
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.FINANCIAL,
                    "risk_level": RiskLevel.LOW,
                    "classification_confidence": 0.9,
                    "extraction": extraction_result,
                    "briefs": briefs_result,
                    "delivery_status": "slack=failed, email=failed",
                    "delivery_success": False,
                }
            )
        ),
    )

    process_filing.run(str(filing_id))

    assert filing.status == FilingStatus.CLASSIFYING


def test_process_filing_stays_classifying_when_delivery_status_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delivery not having run (e.g. state.briefs was None for some other

    reason) must preserve the pre-AGENT-10 CLASSIFYING default rather than
    being mistaken for completion.
    """
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    extraction_result = ExtractionResult(
        obligations=[{"description": "File report.", "source_chunk_index": 0}],
        deadlines=[{"description": "Annual report", "date": "2027-01-01"}],
        risk_flags=["material weakness"],
        affected_products=[],
        key_entities=["Acme Corp"],
        competitor_mentions=[],
        model_used="llama3.1",
    )
    briefs_result = BriefSet(
        executive_brief="Sentence one. Sentence two. Sentence three.",
        cco_summary="Short board-level summary.",
        analyst_summary="- File report by 2027-01-01",
        engineer_summary=f"filing_id={filing_id} domain=financial risk_level=low obligations_extracted=1 status=processed",
        model_used="llama3.1",
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.FINANCIAL,
                    "risk_level": RiskLevel.LOW,
                    "classification_confidence": 0.9,
                    "extraction": extraction_result,
                    "briefs": briefs_result,
                    "delivery_status": None,
                    "delivery_success": None,
                }
            )
        ),
    )

    process_filing.run(str(filing_id))

    assert filing.status == FilingStatus.CLASSIFYING


def test_process_filing_marks_needs_review_when_summarization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    extraction_result = ExtractionResult(
        obligations=[{"description": "File report.", "source_chunk_index": 0}],
        deadlines=[{"description": "Annual report", "date": "2027-01-01"}],
        risk_flags=["material weakness"],
        affected_products=[],
        key_entities=["Acme Corp"],
        competitor_mentions=[],
        model_used="llama3.1",
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.FINANCIAL,
                    "risk_level": RiskLevel.LOW,
                    "classification_confidence": 0.9,
                    "extraction": extraction_result,
                    "briefs": None,
                    "delivery_status": None,
                    "delivery_success": None,
                }
            )
        ),
    )

    process_filing.run(str(filing_id))

    # Extraction still gets persisted even though summarization failed —
    # that successful result must not be discarded.
    assert mock_db.add.call_count == 1
    added_extraction = mock_db.add.call_args[0][0]
    assert isinstance(added_extraction, Extraction)
    assert filing.status == FilingStatus.NEEDS_REVIEW


def test_process_filing_continues_when_embed_chunks_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = "filings/abc123.pdf"

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    monkeypatch.setattr(
        pipeline_tasks_module, "fetch_document_bytes", lambda s3_key: b"fake pdf bytes"
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "extract_text_and_tables",
        lambda pdf_bytes: ("Item 1. Real extracted filing text.", []),
    )
    fake_chunks = [
        Chunk(
            chunk_index=0,
            chunk_text="Item 1. Real extracted filing text.",
            section_reference="Item 1.",
            token_count=6,
            is_table=False,
        )
    ]
    monkeypatch.setattr(
        pipeline_tasks_module, "chunk_filing", lambda text, tables: fake_chunks
    )
    extraction_result = ExtractionResult(
        obligations=[{"description": "File report.", "source_chunk_index": 0}],
        deadlines=[{"description": "Annual report", "date": "2027-01-01"}],
        risk_flags=["material weakness"],
        affected_products=[],
        key_entities=["Acme Corp"],
        competitor_mentions=[],
        model_used="llama3.1",
    )
    briefs_result = BriefSet(
        executive_brief="Sentence one. Sentence two. Sentence three.",
        cco_summary="Short board-level summary.",
        analyst_summary="- File report by 2027-01-01",
        engineer_summary=f"filing_id={filing_id} domain=financial risk_level=low obligations_extracted=1 status=processed",
        model_used="llama3.1",
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.FINANCIAL,
                    "risk_level": RiskLevel.LOW,
                    "classification_confidence": 0.9,
                    "extraction": extraction_result,
                    "briefs": briefs_result,
                    "delivery_status": None,
                    "delivery_success": None,
                }
            )
        ),
    )
    mock_embed_chunks = AsyncMock(side_effect=RuntimeError("embedding service down"))
    monkeypatch.setattr(pipeline_tasks_module, "embed_chunks", mock_embed_chunks)

    # Should not raise/propagate even though embed_chunks fails — a transient
    # embedding failure must not crash process_filing and trigger a Celery
    # retry that would re-run already-committed classification/extraction.
    process_filing.run(str(filing_id))

    mock_embed_chunks.assert_awaited_once()
    # Earlier, already-committed work is unaffected by the embedding failure.
    assert mock_db.add.call_count == 2
    added_extraction = mock_db.add.call_args_list[0].args[0]
    assert isinstance(added_extraction, Extraction)
    assert filing.status == FilingStatus.CLASSIFYING
    assert filing.domain == FilingDomain.FINANCIAL
    assert filing.risk_level == RiskLevel.LOW
    assert filing.classification_confidence == 0.9


def test_process_filing_continues_when_brief_commit_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = "filings/abc123.pdf"

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    # The first commit (filing status) and second commit (Extraction) succeed;
    # the third commit (Brief) raises — isolating the failure to Brief
    # persistence specifically, mirroring the embed_chunks-failure test above.
    mock_db.commit = AsyncMock(side_effect=[None, None, RuntimeError("db write failed")])
    mock_db.add = MagicMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    monkeypatch.setattr(
        pipeline_tasks_module, "fetch_document_bytes", lambda s3_key: b"fake pdf bytes"
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "extract_text_and_tables",
        lambda pdf_bytes: ("Item 1. Real extracted filing text.", []),
    )
    fake_chunks = [
        Chunk(
            chunk_index=0,
            chunk_text="Item 1. Real extracted filing text.",
            section_reference="Item 1.",
            token_count=6,
            is_table=False,
        )
    ]
    monkeypatch.setattr(
        pipeline_tasks_module, "chunk_filing", lambda text, tables: fake_chunks
    )
    extraction_result = ExtractionResult(
        obligations=[{"description": "File report.", "source_chunk_index": 0}],
        deadlines=[{"description": "Annual report", "date": "2027-01-01"}],
        risk_flags=["material weakness"],
        affected_products=[],
        key_entities=["Acme Corp"],
        competitor_mentions=[],
        model_used="llama3.1",
    )
    briefs_result = BriefSet(
        executive_brief="Sentence one. Sentence two. Sentence three.",
        cco_summary="Short board-level summary.",
        analyst_summary="- File report by 2027-01-01",
        engineer_summary=f"filing_id={filing_id} domain=financial risk_level=low obligations_extracted=1 status=processed",
        model_used="llama3.1",
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            ainvoke=AsyncMock(
                return_value={
                    "domain": FilingDomain.FINANCIAL,
                    "risk_level": RiskLevel.LOW,
                    "classification_confidence": 0.9,
                    "extraction": extraction_result,
                    "briefs": briefs_result,
                    "delivery_status": None,
                    "delivery_success": None,
                }
            )
        ),
    )
    mock_embed_chunks = AsyncMock(return_value=None)
    monkeypatch.setattr(pipeline_tasks_module, "embed_chunks", mock_embed_chunks)

    # Should not raise/propagate even though the Brief commit fails — a
    # transient Brief-insert failure must not crash process_filing and
    # trigger a Celery retry that would re-run already-committed
    # classification/extraction and hit Extraction's unique constraint.
    process_filing.run(str(filing_id))

    # Earlier, already-committed work (status + Extraction) is unaffected.
    assert mock_db.add.call_count == 2
    added_extraction = mock_db.add.call_args_list[0].args[0]
    assert isinstance(added_extraction, Extraction)
    added_brief = mock_db.add.call_args_list[1].args[0]
    assert isinstance(added_brief, Brief)
    assert filing.status == FilingStatus.CLASSIFYING
    assert filing.domain == FilingDomain.FINANCIAL
    assert filing.risk_level == RiskLevel.LOW
    assert filing.classification_confidence == 0.9
    # Processing continued past the Brief failure to embed_chunks.
    mock_embed_chunks.assert_awaited_once()


def test_process_filing_calls_chunk_filing_before_graph_invoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = "filings/abc123.pdf"

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    monkeypatch.setattr(
        pipeline_tasks_module, "fetch_document_bytes", lambda s3_key: b"fake pdf bytes"
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "extract_text_and_tables",
        lambda pdf_bytes: ("Item 1. Real extracted filing text.", []),
    )

    fake_chunks = [
        Chunk(
            chunk_index=0,
            chunk_text="Item 1. Real extracted filing text.",
            section_reference="Item 1.",
            token_count=6,
            is_table=False,
        )
    ]
    call_order: list[str] = []

    def _fake_chunk_filing(text, tables):
        call_order.append("chunk_filing")
        return fake_chunks

    captured_state = {}

    async def _fake_ainvoke(state, config=None):
        call_order.append("ainvoke")
        captured_state["chunks"] = state.chunks
        return {
            "domain": FilingDomain.FINANCIAL,
            "risk_level": RiskLevel.LOW,
            "classification_confidence": 0.9,
            "extraction": None,
            "briefs": None,
            "delivery_status": None,
            "delivery_success": None,
        }

    monkeypatch.setattr(pipeline_tasks_module, "chunk_filing", _fake_chunk_filing)
    monkeypatch.setattr(
        pipeline_tasks_module, "build_graph", lambda: MagicMock(ainvoke=_fake_ainvoke)
    )
    mock_embed_chunks = AsyncMock()
    monkeypatch.setattr(pipeline_tasks_module, "embed_chunks", mock_embed_chunks)

    process_filing.run(str(filing_id))

    assert call_order == ["chunk_filing", "ainvoke"]
    assert captured_state["chunks"] == fake_chunks
    mock_embed_chunks.assert_awaited_once_with(filing_id, fake_chunks, mock_db)


def test_process_filing_skips_pipeline_when_filing_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=None)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )

    # Should not raise even though the filing doesn't exist.
    process_filing.run(str(uuid.uuid4()))


async def test_mark_filing_failed_updates_status_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    filing = MagicMock()
    filing.status = None
    filing.processing_error = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )

    await _mark_filing_failed(str(uuid.uuid4()), "something went wrong")

    assert filing.status == FilingStatus.FAILED
    assert filing.processing_error == "something went wrong"
    mock_db.commit.assert_awaited_once()


async def test_mark_filing_failed_handles_missing_filing_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=None)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )

    # Should not raise even though the filing doesn't exist.
    await _mark_filing_failed(str(uuid.uuid4()), "irrelevant")
    mock_db.commit.assert_not_awaited()


def test_on_failure_marks_filing_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    called_with: dict = {}

    async def _fake_mark_filing_failed(filing_id: str, error_message: str) -> None:
        called_with["filing_id"] = filing_id
        called_with["error_message"] = error_message

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(pipeline_tasks_module, "_mark_filing_failed", _fake_mark_filing_failed)

    task_instance = _ProcessFilingTask()
    filing_id = str(uuid.uuid4())
    task_instance.on_failure(
        RuntimeError("boom"), "task-id-123", (filing_id,), {}, None
    )

    assert called_with["filing_id"] == filing_id
    assert "boom" in called_with["error_message"]


def test_on_failure_with_no_filing_id_does_not_raise() -> None:
    task_instance = _ProcessFilingTask()
    # No args/kwargs at all — should log and return, not raise.
    task_instance.on_failure(RuntimeError("boom"), "task-id-123", (), {}, None)
