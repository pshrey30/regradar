"""Unit tests for document extraction (fetch_document_bytes, extract_text_and_tables).

fetch_document_bytes is tested with moto (no real AWS), following the same
settings-env + cache_clear pattern as tests/unit/ingestion/test_pdf_intake.py.
extract_text_and_tables' PDF path is NOT mocked — it runs the real pdfplumber
parser against a real checked-in PDF fixture
(tests/fixtures/sample_filings/synthetic_table_filing.pdf), generated once
via a reportlab script during design. The HTML path uses inline fixtures
since modern SEC EDGAR primary documents are HTML/iXBRL, not PDF.
"""

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from regradar.rag import pdf_extraction
from regradar.rag.chunking import TableBlock
from regradar.rag.pdf_extraction import extract_text_and_tables, fetch_document_bytes

FIXTURE_PDF = (
    Path(__file__).parent.parent.parent / "fixtures" / "sample_filings" / "synthetic_table_filing.pdf"
)

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
    pdf_extraction.get_settings.cache_clear()
    yield
    pdf_extraction.get_settings.cache_clear()


@mock_aws
def test_fetch_document_bytes_downloads_from_s3() -> None:
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket=BUCKET_NAME)
    client.put_object(Bucket=BUCKET_NAME, Key="filings/test.pdf", Body=b"fake pdf bytes")

    result = fetch_document_bytes("filings/test.pdf")

    assert result == b"fake pdf bytes"


def test_extract_text_and_tables_returns_expected_text_and_table_span() -> None:
    pdf_bytes = FIXTURE_PDF.read_bytes()

    text, tables = extract_text_and_tables(pdf_bytes)

    assert text.startswith("Item 7. Management's Discussion and Analysis")
    assert "North America 482.3 6.1 1204" in text
    assert len(tables) == 1
    assert tables[0] == TableBlock(start_char=207, end_char=322)


def test_extract_text_and_tables_table_span_contains_table_content() -> None:
    pdf_bytes = FIXTURE_PDF.read_bytes()

    text, tables = extract_text_and_tables(pdf_bytes)

    table_slice = text[tables[0].start_char : tables[0].end_char]
    assert "Region Revenue($M) Growth(%) Headcount" in table_slice
    assert "Asia Pacific 198.5 9.8 542" in table_slice


def test_extract_text_and_tables_handles_html_bytes() -> None:
    html = b"""
    <html><body>
    <h1>Item 1A. Risk Factors</h1>
    <p>Acme Corp faces a material weakness in internal controls.</p>
    <table><tr><td>Deadline</td><td>2027-01-15</td></tr></table>
    </body></html>
    """

    text, tables = extract_text_and_tables(html)

    assert "Item 1A. Risk Factors" in text
    assert "material weakness" in text
    assert "<html>" not in text
    assert len(tables) == 1
    table_text = text[tables[0].start_char : tables[0].end_char]
    assert "Deadline" in table_text
    assert "2027-01-15" in table_text


def test_extract_text_and_tables_strips_script_and_style_from_html() -> None:
    html = b"""
    <html><body>
    <script>alert('should not appear');</script>
    <style>.hidden { display: none; }</style>
    <p>Real filing content here.</p>
    </body></html>
    """

    text, _tables = extract_text_and_tables(html)

    assert "alert" not in text
    assert "display: none" not in text
    assert "Real filing content here." in text
