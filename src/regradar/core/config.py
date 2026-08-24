"""Pydantic Settings — loads and validates every RegRadar environment variable in one place.

The app should fail fast at import/first-use time if a required variable is
missing, rather than failing later with a confusing error deep in the code.
Secret-shaped values use SecretStr so they never appear in a repr(), str(),
or log line by accident.
"""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from regradar.models.enums import RiskLevel


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────
    env: str = Field(default="development", alias="ENV")
    app_secret_key: SecretStr = Field(alias="APP_SECRET_KEY")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ── Database (Supabase Postgres + pgvector) ─────────────
    database_url: SecretStr = Field(alias="DATABASE_URL")
    database_pool_size: int = Field(default=10, alias="DATABASE_POOL_SIZE")
    supabase_url: str | None = Field(default=None, alias="SUPABASE_URL")
    supabase_service_role_key: SecretStr | None = Field(
        default=None, alias="SUPABASE_SERVICE_ROLE_KEY"
    )

    # ── Redis (Celery broker + result backend) ──────────────
    redis_url: SecretStr = Field(alias="REDIS_URL")

    # ── Object storage (raw PDFs) ───────────────────────────
    s3_bucket_name: str = Field(alias="S3_BUCKET_NAME")
    s3_region: str = Field(alias="S3_REGION")
    aws_access_key_id: SecretStr = Field(alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: SecretStr = Field(alias="AWS_SECRET_ACCESS_KEY")

    # ── LLM providers ────────────────────────────────────────
    openai_api_key: SecretStr = Field(alias="OPENAI_API_KEY")
    huggingface_api_token: SecretStr = Field(alias="HUGGINGFACE_API_TOKEN")

    # ── Model routing tier thresholds ───────────────────────
    tier_high_model: str = Field(default="gpt-4o", alias="TIER_HIGH_MODEL")
    tier_low_model: str = Field(default="granite-13b", alias="TIER_LOW_MODEL")
    tier_routing_risk_threshold: RiskLevel = Field(
        default=RiskLevel.HIGH, alias="TIER_ROUTING_RISK_THRESHOLD"
    )
    classification_confidence_threshold: float = Field(
        default=0.75, alias="CLASSIFICATION_CONFIDENCE_THRESHOLD"
    )

    # ── RAG chunking ─────────────────────────────────────
    chunk_size_tokens: int = Field(default=512, alias="CHUNK_SIZE_TOKENS")
    chunk_overlap_tokens: int = Field(default=50, alias="CHUNK_OVERLAP_TOKENS")
    rag_retrieval_top_k: int = Field(default=5, alias="RAG_RETRIEVAL_TOP_K")

    # ── Local inference — portfolio/demo cost control (ADR-05) ──────
    # When enabled, agents route through locally-hosted models instead of
    # paid APIs, so demo runs cost $0. Off by default; the real routing
    # logic that reads these flags is built alongside AGENT-02/07/08/09.
    use_local_llm: bool = Field(default=False, alias="USE_LOCAL_LLM")
    local_llm_base_url: str = Field(
        default="http://localhost:11434/v1", alias="LOCAL_LLM_BASE_URL"
    )
    local_llm_model: str = Field(default="llama3.1", alias="LOCAL_LLM_MODEL")
    local_llm_low_model: str = Field(default="llama3.2:1b", alias="LOCAL_LLM_LOW_MODEL")
    use_local_hf_inference: bool = Field(default=False, alias="USE_LOCAL_HF_INFERENCE")
    use_local_embeddings: bool = Field(default=False, alias="USE_LOCAL_EMBEDDINGS")
    local_embedding_model: str = Field(default="nomic-embed-text", alias="LOCAL_EMBEDDING_MODEL")

    # ── Ingestion sources ────────────────────────────────────
    sec_edgar_user_agent: str = Field(alias="SEC_EDGAR_USER_AGENT")
    sec_edgar_rate_limit_per_sec: int = Field(default=10, alias="SEC_EDGAR_RATE_LIMIT_PER_SEC")
    fda_rss_feed_url: str | None = Field(default=None, alias="FDA_RSS_FEED_URL")
    finra_feed_url: str | None = Field(default=None, alias="FINRA_FEED_URL")
    finra_client_id: SecretStr | None = Field(default=None, alias="FINRA_CLIENT_ID")
    finra_client_secret: SecretStr | None = Field(default=None, alias="FINRA_CLIENT_SECRET")
    ingestion_poll_interval_seconds: int = Field(
        default=300, alias="INGESTION_POLL_INTERVAL_SECONDS"
    )

    # ── Prefect ──────────────────────────────────────────────
    prefect_api_url: str | None = Field(default=None, alias="PREFECT_API_URL")
    prefect_api_key: SecretStr | None = Field(default=None, alias="PREFECT_API_KEY")

    # ── Delivery ─────────────────────────────────────────────
    slack_webhook_url: SecretStr | None = Field(default=None, alias="SLACK_WEBHOOK_URL")
    slack_bot_token: SecretStr | None = Field(default=None, alias="SLACK_BOT_TOKEN")
    sendgrid_api_key: SecretStr | None = Field(default=None, alias="SENDGRID_API_KEY")
    sendgrid_from_email: str = Field(default="alerts@regradar.io", alias="SENDGRID_FROM_EMAIL")
    delivery_email_recipient: str | None = Field(default=None, alias="DELIVERY_EMAIL_RECIPIENT")
    webhook_hmac_algorithm: str = Field(default="sha256", alias="WEBHOOK_HMAC_ALGORITHM")

    # ── Eval & observability ─────────────────────────────────
    langsmith_api_key: SecretStr | None = Field(default=None, alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="regradar", alias="LANGSMITH_PROJECT")
    langfuse_public_key: str | None = Field(default=None, alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: SecretStr | None = Field(default=None, alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://cloud.langfuse.com", alias="LANGFUSE_HOST")
    daily_cost_alert_threshold_usd: float = Field(
        default=50.0, alias="DAILY_COST_ALERT_THRESHOLD_USD"
    )

    # ── API / auth ────────────────────────────────────────────
    api_rate_limit_per_minute_default: int = Field(
        default=60, alias="API_RATE_LIMIT_PER_MINUTE_DEFAULT"
    )
    api_key_hash_algorithm: str = Field(default="bcrypt", alias="API_KEY_HASH_ALGORITHM")

    # ── Deployment ────────────────────────────────────────────
    fly_app_name_api: str = Field(default="regradar-api", alias="FLY_APP_NAME_API")
    fly_app_name_worker: str = Field(default="regradar-worker", alias="FLY_APP_NAME_WORKER")
    port: int = Field(default=8000, alias="PORT")

    @field_validator("sec_edgar_user_agent")
    @classmethod
    def _require_contact_email_in_user_agent(cls, value: str) -> str:
        if "@" not in value:
            raise ValueError(
                "SEC_EDGAR_USER_AGENT must include a contact email, e.g. "
                '"RegRadar/1.0 (you@example.com)" — EDGAR silently rejects or '
                "rate-limits requests without a descriptive User-Agent."
            )
        return value


@lru_cache
def get_settings() -> Settings:
    """Cached Settings singleton, validated once per process on first use."""
    return Settings()  # type: ignore[call-arg]  # fields are sourced from env / .env, not kwargs
