"""Unit tests for the shared connector Filing-insertion helper."""

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

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from regradar.ingestion.sources._common import insert_new_filing
from regradar.ingestion.types import NewFiling
from regradar.models.enums import FilingSource


def _make_candidate() -> NewFiling:
    return NewFiling(
        source_document_id="0000320193-25-000079",
        entity_name="Apple Inc.",
        filing_type="10-K",
        filing_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/"
            "0000320193-25-000079-index.htm"
        ),
        published_at=datetime.now(UTC),
    )


def _noop_nested_transaction():
    class _Ctx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    return _Ctx()


@pytest.mark.asyncio
async def test_insert_new_filing_returns_filing_on_success() -> None:
    db = AsyncMock()
    db.begin_nested = MagicMock(side_effect=_noop_nested_transaction)
    db.add = MagicMock()
    db.commit = AsyncMock()

    result = await insert_new_filing(db, FilingSource.SEC, _make_candidate())

    assert result is not None
    assert result.source == FilingSource.SEC
    assert result.source_document_id == "0000320193-25-000079"
    assert result.entity_name == "Apple Inc."
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_insert_new_filing_returns_none_on_integrity_error() -> None:
    db = AsyncMock()

    def _raise_nested():
        class _Ctx:
            async def __aenter__(self):
                raise IntegrityError("stmt", {}, Exception("dup"))

            async def __aexit__(self, *args):
                return False

        return _Ctx()

    db.begin_nested = MagicMock(side_effect=_raise_nested)
    db.add = MagicMock()

    result = await insert_new_filing(db, FilingSource.SEC, _make_candidate())

    assert result is None
