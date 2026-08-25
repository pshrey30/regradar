"""Shared enum types used across ORM models, matching the Postgres ENUM types in migrations."""

import enum


def pg_enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """values_callable for SQLAlchemy's Enum column type.

    Without this, SQLAlchemy serializes a Python enum using its *member
    name* (e.g. "INGESTED") rather than its *value* (e.g. "ingested"), which
    doesn't match the lowercase Postgres ENUM types created in migrations —
    every SAEnum(...) column definition in models/ must pass this.
    """
    return [member.value for member in enum_cls]


class FilingSource(str, enum.Enum):
    SEC = "SEC"
    FDA = "FDA"
    FINRA = "FINRA"


class FilingStatus(str, enum.Enum):
    INGESTED = "ingested"
    CLASSIFYING = "classifying"
    NEEDS_CLASSIFICATION = "needs_classification"
    NEEDS_REVIEW = "needs_review"
    RETRIEVING = "retrieving"
    ANALYZING = "analyzing"
    SUMMARIZING = "summarizing"
    DELIVERING = "delivering"
    COMPLETE = "complete"
    FAILED = "failed"


class FilingDomain(str, enum.Enum):
    FINANCIAL = "financial"
    CLINICAL = "clinical"
    ENVIRONMENTAL = "environmental"
    OTHER = "other"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DeliveryChannel(str, enum.Enum):
    SLACK = "slack"
    EMAIL = "email"
    WEBHOOK = "webhook"


class DeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    RETRYING = "retrying"


class EvalRunType(str, enum.Enum):
    PRE_DEPLOY_REGRESSION = "pre_deploy_regression"
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class ApiKeyRole(str, enum.Enum):
    ADMIN = "admin"
    ANALYST = "analyst"
    EXECUTIVE = "executive"
    LEGAL_COUNSEL = "legal_counsel"
    ENG_LEAD = "eng_lead"
