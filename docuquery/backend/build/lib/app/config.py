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

    # App
    app_name: str = "DocuQuery"
    debug: bool = True
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    upload_dir: Path = BACKEND_DIR / "uploads"
    max_upload_mb: int = 50

    # LLM (OpenAI-compatible endpoint: Ollama, Z.ai, OpenAI, ...)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:3b"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1024

    # Embeddings (OpenAI-compatible; EMBED_DIM must match the model's output)
    embeddings_base_url: str = "http://localhost:11434/v1"
    embeddings_api_key: str = "ollama"
    embeddings_model: str = "nomic-embed-text"
    embed_dim: int = 768

    # Infrastructure
    database_url: str = "postgresql+psycopg://docuquery:docuquery@localhost:5433/docuquery"
    redis_url: str = "redis://localhost:6379/0"

    # Ingestion / chunking
    chunk_size: int = 1000
    chunk_overlap: int = 150
    ocr_dpi: int = 200
    ocr_min_chars_per_page: int = 32

    # Auth
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24 * 7

    @property
    def sync_database_url(self) -> str:
        """psycopg3 works sync and async; Alembic/Celery use the plain sync form."""
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings
