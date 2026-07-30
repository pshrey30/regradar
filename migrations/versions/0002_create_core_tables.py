"""Create the nine core RegRadar tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ENUM_TYPES: dict[str, list[str]] = {
    "filing_source": ["SEC", "FDA", "FINRA"],
    "filing_status": [
        "ingested",
        "classifying",
        "retrieving",
        "analyzing",
        "summarizing",
        "delivering",
        "complete",
        "failed",
    ],
    "filing_domain": ["financial", "clinical", "environmental", "other"],
    "risk_level": ["low", "medium", "high", "critical"],
    "delivery_channel": ["slack", "email", "webhook"],
    "delivery_status": ["pending", "sent", "failed", "retrying"],
    "eval_run_type": ["pre_deploy_regression", "scheduled", "manual"],
}


def _enum(name: str) -> postgresql.ENUM:
    """Reference an already-created Postgres ENUM type without re-issuing CREATE TYPE."""
    return postgresql.ENUM(*ENUM_TYPES[name], name=name, create_type=False)


def upgrade() -> None:
    for name, values in ENUM_TYPES.items():
        values_sql = ", ".join(f"'{v}'" for v in values)
        op.execute(f"CREATE TYPE {name} AS ENUM ({values_sql})")

    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("owner_label", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_table(
        "filings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", _enum("filing_source"), nullable=False),
        sa.Column("source_document_id", sa.Text(), nullable=False),
        sa.Column("entity_name", sa.Text(), nullable=False),
        sa.Column("filing_type", sa.Text(), nullable=False),
        sa.Column("filing_url", sa.Text(), nullable=False),
        sa.Column("raw_pdf_s3_key", sa.Text(), nullable=True),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "status",
            _enum("filing_status"),
            nullable=False,
            server_default="ingested",
        ),
        sa.Column("domain", _enum("filing_domain"), nullable=True),
        sa.Column("risk_level", _enum("risk_level"), nullable=True),
        sa.Column("priority_score", sa.Float(), nullable=True),
        sa.Column("classification_confidence", sa.Float(), nullable=True),
        sa.Column("processing_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("source", "source_document_id", name="uq_filings_source_document_id"),
    )

    op.create_table(
        "filing_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "filing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("filings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("section_reference", sa.Text(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("is_table", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_filing_chunks_filing_id", "filing_chunks", ["filing_id"])

    op.create_table(
        "extractions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "filing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("filings.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("obligations", postgresql.JSONB(), nullable=True),
        sa.Column("deadlines", postgresql.JSONB(), nullable=True),
        sa.Column("risk_flags", postgresql.JSONB(), nullable=True),
        sa.Column("affected_products", postgresql.JSONB(), nullable=True),
        sa.Column("key_entities", postgresql.JSONB(), nullable=True),
        sa.Column("competitor_mentions", postgresql.JSONB(), nullable=True),
        sa.Column("model_used", sa.Text(), nullable=True),
        sa.Column("raw_model_response", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "briefs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "filing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("filings.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("executive_brief", sa.Text(), nullable=False),
        sa.Column("cco_summary", sa.Text(), nullable=False),
        sa.Column("analyst_summary", sa.Text(), nullable=False),
        sa.Column("engineer_summary", sa.Text(), nullable=False),
        sa.Column("model_used", sa.Text(), nullable=True),
        sa.Column("rouge_l_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "webhooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "api_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("hmac_secret", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("filter_domain", sa.Text(), nullable=True),
        sa.Column("filter_min_risk", _enum("risk_level"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_webhooks_api_key_id", "webhooks", ["api_key_id"])

    op.create_table(
        "deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "filing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("filings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", _enum("delivery_channel"), nullable=False),
        sa.Column(
            "webhook_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("webhooks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("recipient", sa.Text(), nullable=False),
        sa.Column(
            "status",
            _enum("delivery_status"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_deliveries_filing_id", "deliveries", ["filing_id"])
    op.create_index("ix_deliveries_webhook_id", "deliveries", ["webhook_id"])

    op.create_table(
        "source_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", _enum("filing_source"), nullable=False),
        sa.Column("domains", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("last_polled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_etag", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "eval_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_type", _enum("eval_run_type"), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("git_commit_sha", sa.Text(), nullable=True),
        sa.Column("ragas_faithfulness", sa.Float(), nullable=True),
        sa.Column("ragas_context_recall", sa.Float(), nullable=True),
        sa.Column("rouge_l", sa.Float(), nullable=True),
        sa.Column("alert_precision", sa.Float(), nullable=True),
        sa.Column("alert_recall", sa.Float(), nullable=True),
        sa.Column("hallucination_rate", sa.Float(), nullable=True),
        sa.Column("extraction_f1", sa.Float(), nullable=True),
        sa.Column("p99_latency_ms", sa.Integer(), nullable=True),
        sa.Column("avg_cost_per_filing_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("eval_runs")
    op.drop_table("source_configs")
    op.drop_index("ix_deliveries_webhook_id", table_name="deliveries")
    op.drop_index("ix_deliveries_filing_id", table_name="deliveries")
    op.drop_table("deliveries")
    op.drop_index("ix_webhooks_api_key_id", table_name="webhooks")
    op.drop_table("webhooks")
    op.drop_table("briefs")
    op.drop_table("extractions")
    op.drop_index("ix_filing_chunks_filing_id", table_name="filing_chunks")
    op.drop_table("filing_chunks")
    op.drop_table("filings")
    op.drop_table("api_keys")

    for name in ENUM_TYPES:
        op.execute(f"DROP TYPE {name}")
