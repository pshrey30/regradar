"""S3 client factory for storing raw filing PDFs."""

from typing import Any

import boto3

from regradar.core.config import get_settings

_client: Any = None


def get_s3_client() -> Any:
    global _client
    if _client is None:
        settings = get_settings()
        _client = boto3.client(
            "s3",
            region_name=settings.s3_region,
            aws_access_key_id=settings.aws_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.aws_secret_access_key.get_secret_value(),
        )
    return _client


def generate_presigned_pdf_url(s3_key: str, *, expires_in: int = 300) -> str:
    """A short-lived signed URL for a filing's raw PDF (FE-04's "View
    Original Document" link) — never a permanent/public one, since the
    bucket holding these isn't public."""
    settings = get_settings()
    return get_s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket_name, "Key": s3_key},
        ExpiresIn=expires_in,
    )
