"""Document text and table extraction — reads a filing's stored S3 document
(real PDF binary, or HTML for modern SEC EDGAR primary documents — most
2020s+ EDGAR filings are HTML/iXBRL, not PDF; SEC deprecated PDF-only
primary filings years ago) and turns it into plain text plus detected
table regions, ready for rag.chunking.chunk_filing().

Dispatches on the actual downloaded bytes' real content (PDF magic bytes),
never on file extension or URL — a redirect or misconfigured content-type
header could make either assumption wrong.
"""

import io

import pdfplumber
from bs4 import BeautifulSoup

from regradar.core.config import get_settings
from regradar.core.s3_client import get_s3_client
from regradar.rag.chunking import TableBlock

PDF_MAGIC_BYTES = b"%PDF-"


def fetch_document_bytes(s3_key: str) -> bytes:
    """Download a filing's stored document (PDF or HTML) from S3."""
    settings = get_settings()
    client = get_s3_client()
    response = client.get_object(Bucket=settings.s3_bucket_name, Key=s3_key)
    return response["Body"].read()


def _extract_from_pdf(pdf_bytes: bytes) -> tuple[str, list[TableBlock]]:
    """Per page, page.extract_text() builds that page's text (pages joined
    with "\\n\\n"). For each page.find_tables() result,
    page.within_bbox(table.bbox).extract_text() gives that table's own
    rendered text, located within the page's full text via str.find() —
    both use the same extract_text() rendering, so this locates reliably.
    A table whose text can't be found in the page text is skipped, never
    guessed at."""
    full_text_parts: list[str] = []
    tables: list[TableBlock] = []
    cumulative_offset = 0

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            for table in page.find_tables():
                table_text = page.within_bbox(table.bbox).extract_text() or ""
                if not table_text:
                    continue
                local_start = page_text.find(table_text)
                if local_start == -1:
                    continue
                local_end = local_start + len(table_text)
                tables.append(
                    TableBlock(
                        start_char=cumulative_offset + local_start,
                        end_char=cumulative_offset + local_end,
                    )
                )
            full_text_parts.append(page_text)
            cumulative_offset += len(page_text) + 2  # +2 for the "\n\n" page separator

    full_text = "\n\n".join(full_text_parts)
    return full_text, tables


def _extract_from_html(html_bytes: bytes) -> tuple[str, list[TableBlock]]:
    """Strip script/style (never real filing content), extract visible
    text, then locate each <table>'s own text within that same full text
    — same start_char/end_char contract as the PDF path, located via
    str.find() the same way, for the same reason (one extraction pass is
    the ground truth both the full text and each table's span come from)."""
    soup = BeautifulSoup(html_bytes, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    table_texts = [table.get_text(separator="\n", strip=True) for table in soup.find_all("table")]

    full_text = soup.get_text(separator="\n", strip=True)

    tables: list[TableBlock] = []
    for table_text in table_texts:
        if not table_text:
            continue
        start = full_text.find(table_text)
        if start == -1:
            continue
        tables.append(TableBlock(start_char=start, end_char=start + len(table_text)))

    return full_text, tables


def extract_text_and_tables(document_bytes: bytes) -> tuple[str, list[TableBlock]]:
    """Extract plain text and detected table regions from a filing's
    downloaded document — dispatches on the bytes' actual content, not
    file extension or URL."""
    if document_bytes.lstrip()[: len(PDF_MAGIC_BYTES)] == PDF_MAGIC_BYTES:
        return _extract_from_pdf(document_bytes)
    return _extract_from_html(document_bytes)
