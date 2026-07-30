"""Shared enum types used across ORM models, matching the Postgres ENUM types in migrations."""

import enum


class FilingSource(str, enum.Enum):
    SEC = "SEC"
    FDA = "FDA"
    FINRA = "FINRA"


class FilingStatus(str, enum.Enum):
    INGESTED = "ingested"
    CLASSIFYING = "classifying"
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
