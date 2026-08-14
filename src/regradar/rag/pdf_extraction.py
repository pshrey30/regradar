"""PDF text and table extraction — reads a filing's stored S3 PDF and
turns it into plain text plus detected table regions, ready for
rag.chunking.chunk_filing().
"""

import io

import pdfplumber

from regradar.core.config import get_settings
from regradar.core.s3_client import get_s3_client
from regradar.rag.chunking import TableBlock


def fetch_pdf_bytes(s3_key: str) -> bytes:
    """Download a filing's PDF from S3."""
    settings = get_settings()
    client = get_s3_client()
    response = client.get_object(Bucket=settings.s3_bucket_name, Key=s3_key)
    return response["Body"].read()


def extract_text_and_tables(pdf_bytes: bytes) -> tuple[str, list[TableBlock]]:
    """Extract plain text and detected table regions from a PDF.

    Per page, page.extract_text() builds that page's text (pages joined
    with "\\n\\n"). For each page.find_tables() result,
    page.within_bbox(table.bbox).extract_text() gives that table's own
    rendered text, located within the page's full text via str.find() —
    both use the same extract_text() rendering, so this locates reliably.
    A table whose text can't be found in the page text is skipped, never
    guessed at.
    """
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
