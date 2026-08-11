"""PDF intake: download, content-hash, dedup against S3, upload if new.

Shared by every ingestion connector rather than duplicated per-source —
none of the three connectors currently call this (EDGAR/FDA/FINRA store
only metadata + a source URL today), but it's the module ING-05 exists to
provide, ready for a connector or the pipeline worker to call once a
filing needs its actual PDF archived.
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


def _download_pdf(url: str) -> bytes:
    response = httpx.get(url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    return response.content


def _download_with_retry(url: str) -> bytes:
    try:
        return _download_pdf(url)
    except httpx.HTTPError:
        logger.warning("First download attempt failed for %s, retrying once", url)
        time.sleep(1)
        return _download_pdf(url)  # let a second failure propagate to the caller


def _s3_key_for_hash(content_hash: str) -> str:
    return f"filings/{content_hash}.pdf"


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
        content = _download_with_retry(url)
    except httpx.HTTPError as exc:
        error_message = f"Failed to download PDF from {url}: {exc}"
        logger.warning(error_message)
        filing = await db.get(Filing, filing_id)
        if filing is not None:
            filing.status = FilingStatus.FAILED
            filing.processing_error = error_message
            await db.commit()
        raise PdfIntakeError(error_message) from exc

    content_hash = hashlib.sha256(content).hexdigest()
    s3_key = _s3_key_for_hash(content_hash)

    s3_client = get_s3_client()
    bucket = settings.s3_bucket_name

    if _object_exists(s3_client, bucket, s3_key):
        logger.info("PDF with hash %s already in S3, skipping re-upload", content_hash)
    else:
        s3_client.put_object(
            Bucket=bucket, Key=s3_key, Body=content, ContentType="application/pdf"
        )

    filing = await db.get(Filing, filing_id)
    if filing is not None:
        filing.raw_pdf_s3_key = s3_key
        await db.commit()

    return s3_key
