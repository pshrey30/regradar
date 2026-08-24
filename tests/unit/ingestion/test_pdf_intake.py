"""Unit tests for pdf_intake — S3 content-hash dedup, fully moto-mocked (no real AWS)."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import boto3
import httpx
import pytest
from moto import mock_aws

from regradar.ingestion import pdf_intake
from regradar.models.enums import FilingStatus

SAMPLE_PDF_CONTENT = b"%PDF-1.4 fake pdf content for testing"
BUCKET_NAME = "test-regradar-bucket"


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_SECRET_KEY", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("S3_BUCKET_NAME", BUCKET_NAME)
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "test")
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "RegRadar/1.0 (test@example.com)")
    pdf_intake.get_settings.cache_clear()
    yield
    pdf_intake.get_settings.cache_clear()


def _make_mock_db(filing) -> AsyncMock:
    db = AsyncMock()
    db.get = AsyncMock(return_value=filing)
    db.commit = AsyncMock()
    return db


def _make_filing_stub():
    filing = MagicMock()
    filing.status = None
    filing.processing_error = None
    filing.raw_pdf_s3_key = None
    return filing


def _mock_http_response() -> MagicMock:
    response = MagicMock(status_code=200, content=SAMPLE_PDF_CONTENT)
    response.raise_for_status = MagicMock()
    return response


async def test_successful_upload_sets_raw_pdf_s3_key(monkeypatch: pytest.MonkeyPatch) -> None:
    with mock_aws():
        real_s3 = boto3.client("s3", region_name="us-east-1")
        real_s3.create_bucket(Bucket=BUCKET_NAME)
        monkeypatch.setattr(pdf_intake, "get_s3_client", lambda: real_s3)
        monkeypatch.setattr(pdf_intake.httpx, "get", MagicMock(return_value=_mock_http_response()))

        filing = _make_filing_stub()
        db = _make_mock_db(filing)

        key = await pdf_intake.intake_pdf("https://example.com/filing.pdf", uuid.uuid4(), db)

        assert key.startswith("filings/")
        assert key.endswith(".pdf")
        assert filing.raw_pdf_s3_key == key
        db.commit.assert_awaited()

        obj = real_s3.get_object(Bucket=BUCKET_NAME, Key=key)
        assert obj["Body"].read() == SAMPLE_PDF_CONTENT
        assert obj["ContentType"] == "application/pdf"


async def test_html_document_is_stored_with_html_extension_and_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: most modern SEC EDGAR primary documents are HTML,
    not PDF (verified live) — a real EDGAR document downloaded and stored
    this way must not be mislabeled as .pdf/application/pdf, or it fails
    to open as a PDF even though it downloaded successfully."""
    html_content = b"<!DOCTYPE html><html><body><h1>Item 1A. Risk Factors</h1></body></html>"
    response = MagicMock(status_code=200, content=html_content)
    response.raise_for_status = MagicMock()

    with mock_aws():
        real_s3 = boto3.client("s3", region_name="us-east-1")
        real_s3.create_bucket(Bucket=BUCKET_NAME)
        monkeypatch.setattr(pdf_intake, "get_s3_client", lambda: real_s3)
        monkeypatch.setattr(pdf_intake.httpx, "get", MagicMock(return_value=response))

        filing = _make_filing_stub()
        db = _make_mock_db(filing)

        key = await pdf_intake.intake_pdf("https://example.com/filing.htm", uuid.uuid4(), db)

        assert key.endswith(".html")
        obj = real_s3.get_object(Bucket=BUCKET_NAME, Key=key)
        assert obj["ContentType"] == "text/html"
        assert obj["Body"].read() == html_content


async def test_xml_document_is_stored_with_xml_extension_and_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: EDGAR's structured forms (Form 4, Form D, etc.)
    serve their primary document as XML — same mislabeling risk as HTML."""
    xml_content = b"<?xml version=\"1.0\"?><edgarSubmission><item>Form D</item></edgarSubmission>"
    response = MagicMock(status_code=200, content=xml_content)
    response.raise_for_status = MagicMock()

    with mock_aws():
        real_s3 = boto3.client("s3", region_name="us-east-1")
        real_s3.create_bucket(Bucket=BUCKET_NAME)
        monkeypatch.setattr(pdf_intake, "get_s3_client", lambda: real_s3)
        monkeypatch.setattr(pdf_intake.httpx, "get", MagicMock(return_value=response))

        filing = _make_filing_stub()
        db = _make_mock_db(filing)

        key = await pdf_intake.intake_pdf(
            "https://example.com/primary_doc.xml", uuid.uuid4(), db
        )

        assert key.endswith(".xml")
        obj = real_s3.get_object(Bucket=BUCKET_NAME, Key=key)
        assert obj["ContentType"] == "application/xml"
        assert obj["Body"].read() == xml_content


async def test_download_sends_descriptive_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: real SEC EDGAR returns 403 Forbidden on any document
    download with no User-Agent header — verified live during PDF-intake
    wiring, even though this download function had never sent one."""
    mock_get = MagicMock(return_value=_mock_http_response())
    monkeypatch.setattr(pdf_intake.httpx, "get", mock_get)

    with mock_aws():
        real_s3 = boto3.client("s3", region_name="us-east-1")
        real_s3.create_bucket(Bucket=BUCKET_NAME)
        monkeypatch.setattr(pdf_intake, "get_s3_client", lambda: real_s3)

        filing = _make_filing_stub()
        db = _make_mock_db(filing)
        await pdf_intake.intake_pdf("https://example.com/filing.pdf", uuid.uuid4(), db)

    call_headers = mock_get.call_args.kwargs["headers"]
    assert call_headers["User-Agent"] == "RegRadar/1.0 (test@example.com)"


async def test_duplicate_content_is_not_reuploaded(monkeypatch: pytest.MonkeyPatch) -> None:
    with mock_aws():
        real_s3 = boto3.client("s3", region_name="us-east-1")
        real_s3.create_bucket(Bucket=BUCKET_NAME)
        spy_client = MagicMock(wraps=real_s3)
        monkeypatch.setattr(pdf_intake, "get_s3_client", lambda: spy_client)
        monkeypatch.setattr(pdf_intake.httpx, "get", MagicMock(return_value=_mock_http_response()))

        filing1 = _make_filing_stub()
        db1 = _make_mock_db(filing1)
        key1 = await pdf_intake.intake_pdf("https://example.com/filing.pdf", uuid.uuid4(), db1)

        filing2 = _make_filing_stub()
        db2 = _make_mock_db(filing2)
        key2 = await pdf_intake.intake_pdf(
            "https://example.com/filing-again.pdf", uuid.uuid4(), db2
        )

        assert key1 == key2  # identical content -> identical hash -> identical key
        assert spy_client.put_object.call_count == 1  # only the first call actually uploaded
        assert filing2.raw_pdf_s3_key == key2


async def test_failed_download_marks_filing_failed_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args, **kwargs):
        raise httpx.ConnectError("simulated failure")

    monkeypatch.setattr(pdf_intake.httpx, "get", _raise)
    monkeypatch.setattr(pdf_intake.time, "sleep", lambda _: None)

    filing = _make_filing_stub()
    db = _make_mock_db(filing)

    with pytest.raises(pdf_intake.PdfIntakeError):
        await pdf_intake.intake_pdf("https://example.com/filing.pdf", uuid.uuid4(), db)

    assert filing.status == FilingStatus.FAILED
    assert filing.processing_error is not None
    db.commit.assert_awaited()


async def test_download_retried_once_before_succeeding(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = {"n": 0}

    def _flaky_get(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ConnectError("simulated transient failure")
        return _mock_http_response()

    monkeypatch.setattr(pdf_intake.httpx, "get", _flaky_get)
    monkeypatch.setattr(pdf_intake.time, "sleep", lambda _: None)

    with mock_aws():
        real_s3 = boto3.client("s3", region_name="us-east-1")
        real_s3.create_bucket(Bucket=BUCKET_NAME)
        monkeypatch.setattr(pdf_intake, "get_s3_client", lambda: real_s3)

        filing = _make_filing_stub()
        db = _make_mock_db(filing)

        key = await pdf_intake.intake_pdf("https://example.com/filing.pdf", uuid.uuid4(), db)

    assert call_count["n"] == 2
    assert filing.raw_pdf_s3_key == key
