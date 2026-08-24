"""Document intake: download, content-hash, dedup against S3, upload if new.

Shared by every ingestion connector — wired into sec_edgar.py, the only
source with a real per-filing document to archive (see fda_rss.py/
finra_feed.py's module docstrings for why it doesn't apply there).

Despite the module's original "PDF" framing (ING-05), the archived
content is not always a real PDF — verified live: most modern SEC EDGAR
primary documents are HTML or XML, not PDF (SEC deprecated PDF-only
primary filings years ago). The stored S3 key's extension and
Content-Type are derived from the actual downloaded bytes (see
_classify_document), not assumed to be PDF — an earlier version of this
module hardcoded both to .pdf/application/pdf unconditionally, which
silently mislabeled every non-PDF document archived this way: the file
would download fine but fail to open as a PDF, since it never was one.
rag/pdf_extraction.py already sniffs real content the same way, so
extraction was never affected — only the S3 object's own labeling was.
"""

import hashlib
import logging
import time
import uuid

import httpx
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.core.config import get_settings
from regradar.core.s3_client import get_s3_client
from regradar.models.enums import FilingStatus
from regradar.models.filing import Filing

logger = logging.getLogger(__name__)


class PdfIntakeError(Exception):
    """Raised when a PDF can't be downloaded after retrying once."""


def _download_pdf(url: str, user_agent: str) -> bytes:
    # SEC EDGAR (and many other sites) return 403 Forbidden on any request
    # without a descriptive User-Agent — verified live: every real EDGAR
    # document download failed this way until this header was added, even
    # though the feed/API requests elsewhere in this project already send
    # one. Sending it unconditionally, regardless of source, is harmless
    # for sources that don't require it.
    response = httpx.get(
        url, headers={"User-Agent": user_agent}, timeout=30.0, follow_redirects=True
    )
    response.raise_for_status()
    return response.content


def _download_with_retry(url: str, user_agent: str) -> bytes:
    try:
        return _download_pdf(url, user_agent)
    except httpx.HTTPError:
        logger.warning("First download attempt failed for %s, retrying once", url)
        time.sleep(1)
        return _download_pdf(url, user_agent)  # let a second failure propagate to the caller


PDF_MAGIC_BYTES = b"%PDF-"
XML_DECLARATION = b"<?xml"


def _classify_document(content: bytes) -> tuple[str, str]:
    """(file_extension, content_type) based on the real downloaded bytes.

    Real PDF binary -> pdf/application/pdf. An XML declaration (common for
    EDGAR's structured forms, e.g. Form 4/Form D primary_doc.xml) ->
    xml/application/xml. Anything else -> html/text/html, since that's
    what EDGAR actually serves for narrative filings (10-K/10-Q/8-K/etc.)
    and it's a reasonable, openable default for arbitrary markup.
    """
    stripped = content.lstrip()
    if stripped[: len(PDF_MAGIC_BYTES)] == PDF_MAGIC_BYTES:
        return "pdf", "application/pdf"
    if stripped[: len(XML_DECLARATION)] == XML_DECLARATION:
        return "xml", "application/xml"
    return "html", "text/html"


def _s3_key_for_hash(content_hash: str, extension: str) -> str:
    return f"filings/{content_hash}.{extension}"


def _object_exists(s3_client, bucket: str, key: str) -> bool:
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


async def intake_pdf(url: str, filing_id: uuid.UUID, db: AsyncSession) -> str:
    """Download a filing's PDF, dedup by content hash, upload to S3 if new.

    Returns the S3 key. filings.raw_pdf_s3_key is only ever set after a
    successful upload (or a confirmed-existing dedup hit) — never left
    pointing at an object that doesn't exist. On a download failure after
    one retry, marks the filing status=failed with processing_error
    populated and raises PdfIntakeError, rather than leaving it in limbo.
    """
    settings = get_settings()

    try:
        content = _download_with_retry(url, settings.sec_edgar_user_agent)
    except httpx.HTTPError as exc:
        error_message = f"Failed to download document from {url}: {exc}"
        logger.warning(error_message)
        filing = await db.get(Filing, filing_id)
        if filing is not None:
            filing.status = FilingStatus.FAILED
            filing.processing_error = error_message
            await db.commit()
        raise PdfIntakeError(error_message) from exc

    content_hash = hashlib.sha256(content).hexdigest()
    extension, content_type = _classify_document(content)
    s3_key = _s3_key_for_hash(content_hash, extension)

    s3_client = get_s3_client()
    bucket = settings.s3_bucket_name

    if _object_exists(s3_client, bucket, s3_key):
        logger.info("Document with hash %s already in S3, skipping re-upload", content_hash)
    else:
        s3_client.put_object(Bucket=bucket, Key=s3_key, Body=content, ContentType=content_type)

    filing = await db.get(Filing, filing_id)
    if filing is not None:
        filing.raw_pdf_s3_key = s3_key
        await db.commit()

    return s3_key
