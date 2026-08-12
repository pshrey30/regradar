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

from regradar.models.enums import FilingDomain, FilingStatus, RiskLevel
from regradar.models.filing import Filing
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
            invoke=lambda state: {
                "domain": FilingDomain.FINANCIAL,
                "risk_level": RiskLevel.LOW,
                "classification_confidence": 0.9,
            }
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
            invoke=lambda state: {
                "domain": None,
                "risk_level": None,
                "classification_confidence": None,
            }
        ),
    )

    process_filing.run(str(filing_id))

    assert filing.status == FilingStatus.NEEDS_CLASSIFICATION
    mock_db.commit.assert_awaited_once()


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
