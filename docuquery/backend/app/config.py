from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Central config. Every value can be overridden via .env or environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App / edition
    app_name: str = "DocuQuery"
    # Product editions gate optional feature surfaces. Security fundamentals are
    # always enabled; see app/editions.py for the feature -> edition mapping.
    edition: str = "enterprise"  # "basic" | "professional" | "enterprise"
    environment: str = "local"  # "local" | "staging" | "production"
    debug: bool = True
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    upload_dir: Path = BACKEND_DIR / "uploads"
    max_upload_mb: int = 50
    max_pdf_pages: int = 500

    # LLM (OpenAI-compatible endpoint: Ollama, Z.ai, OpenAI, ...)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:3b"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1024

    # Cost analytics (USD per 1k tokens; 0 for free local models)
    llm_cost_prompt_per_1k: float = 0.0
    llm_cost_completion_per_1k: float = 0.0

    # Embeddings (OpenAI-compatible; EMBED_DIM must match the model's output)
    embeddings_base_url: str = "http://localhost:11434/v1"
    embeddings_api_key: str = "ollama"
    embeddings_model: str = "nomic-embed-text"
    embed_dim: int = 768

    # Infrastructure
    database_url: str = "postgresql+psycopg://docuquery:docuquery@localhost:5433/docuquery"
    redis_url: str = "redis://localhost:6379/0"
    use_celery: bool = False  # By default in dev, use in-process BackgroundTasks to avoid hung queues

    # Ingestion / chunking
    chunk_size: int = 1000
    chunk_overlap: int = 150
    ocr_dpi: int = 200
    ocr_min_chars_per_page: int = 32

    # Office/HTML ingestion guards: OOXML files are ZIP containers, so cap the
    # uncompressed payload and entry count to reject zip bombs.
    max_uncompressed_mb: int = 200
    max_archive_entries: int = 2000
    xlsx_rows_per_section: int = 50  # rows per chunk section; header is repeated

    # URL import (POST /api/documents/url)
    url_import_enabled: bool = True
    url_fetch_timeout_seconds: int = 20

    # Retrieval quality
    # Follow-up rewriter: turns "what about page 2?" into a standalone query
    # using recent conversation history. Falls back to the original question.
    query_rewrite_enabled: bool = True
    query_rewrite_timeout_seconds: int = 8
    # Optional local cross-encoder reranker (needs the `sentence-transformers`
    # extra: pip install -e ".[rerank]"). Disabled by default; any failure
    # falls back to the fused RRF order.
    reranker_enabled: bool = False
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # Structured extraction (professional edition). Chunks per document fed to
    # the model, in document order; extraction answers get their own token cap.
    extract_max_chunks_per_doc: int = 30
    extract_max_tokens: int = 2000

    # Outgoing webhooks (document.ready / document.failed)
    webhook_max_attempts: int = 3
    webhook_retry_backoff_seconds: float = 2.0
    webhook_timeout_seconds: int = 10

    # Upload malware scanning (optional ClamAV clamd via TCP INSTREAM).
    clamav_enabled: bool = False
    clamav_host: str = "localhost"
    clamav_port: int = 3310
    clamav_timeout_seconds: int = 5

    # Eval gate: scripts/run_eval.py exits non-zero when hit@6 falls below
    # this fraction. Raise toward 1.0 as the corpus and golden set mature.
    eval_min_hit6: float = 0.5
    # Optional LLM-as-judge pass (faithfulness / answer relevance / refusals).
    eval_judge_enabled: bool = False
    eval_judge_model: str | None = None  # defaults to LLM_MODEL

    # Production boot guards. https_enabled asserts TLS terminates in front of
    # this deployment (reverse proxy or load balancer).
    https_enabled: bool = False

    # Auth
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    # Short-lived access token; the refresh flow (HttpOnly cookie) extends the
    # session. 30 minutes bounds the blast radius of a leaked access token.
    jwt_expires_minutes: int = 30
    refresh_token_expires_days: int = 30
    password_min_length: int = 10
    login_max_attempts: int = 5
    login_lockout_seconds: int = 900  # 15 minutes
    # Emails listed here are created with the admin role at registration.
    admin_emails: list[str] = []

    # Rate limiting (requests per window, per identity)
    rate_limit_auth: int = 10
    rate_limit_chat: int = 30
    rate_limit_upload: int = 20
    rate_limit_search: int = 60
    rate_limit_window_seconds: int = 60

    # Quotas (0 = unlimited). Professional edition enforces these.
    max_daily_questions: int = 0
    max_uploads_per_day: int = 0

    # Semantic answer cache (professional edition). Reduces duplicate-question cost.
    cache_enabled: bool = True
    cache_ttl_minutes: int = 60
    cache_similarity_threshold: float = 0.92  # cosine similarity required for a hit

    # Audit log retention (0 = keep forever)
    audit_retention_days: int = 0

    @property
    def sync_database_url(self) -> str:
        """psycopg3 works sync and async; Alembic/Celery use the plain sync form."""
        return self.database_url

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


def _validate_production(settings: "Settings") -> None:
    """Refuse to boot a production deployment with insecure configuration."""
    if not settings.is_production:
        return
    if settings.jwt_secret == "change-me-in-production":
        raise RuntimeError(
            "Refusing to start in production with the default JWT_SECRET. "
            "Set a strong JWT_SECRET in the environment."
        )
    if settings.debug:
        raise RuntimeError("DEBUG must be False in production.")
    if "*" in settings.cors_origins:
        raise RuntimeError(
            "Refusing to start in production with a wildcard CORS origin. "
            "List the exact frontend origins in CORS_ORIGINS."
        )
    database_url = settings.database_url
    if ":" in database_url:
        # postgresql+psycopg://user:password@host/db
        try:
            after_scheme = database_url.split("://", 1)[1]
            password = after_scheme.split("@", 1)[0].split(":", 1)[1]
        except IndexError:
            password = ""
        if password.strip() in ("", "docuquery", "postgres", "password", "changeme"):
            raise RuntimeError(
                "Refusing to start in production with a weak/default database "
                "password. Set a strong password in DATABASE_URL."
            )
    if not settings.https_enabled:
        raise RuntimeError(
            "Refusing to start in production without HTTPS. Terminate TLS in a "
            "reverse proxy and set HTTPS_ENABLED=True in the environment."
        )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    _validate_production(settings)
    try:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return settings
