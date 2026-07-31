"""Shared types for ingestion source connectors."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class NewFiling:
    """A candidate filing parsed from a source feed, not yet confirmed persisted."""

    source_document_id: str
    entity_name: str
    filing_type: str
    filing_url: str
    published_at: datetime
