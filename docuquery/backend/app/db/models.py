import uuid
from datetime import datetime
from enum import Enum as PyEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


class UserRole(str, PyEnum):
    user = "user"
    admin = "admin"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(120), default=None)
    role: Mapped[str] = mapped_column(String(16), default=UserRole.user.value, server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    documents: Mapped[list["Document"]] = relationship(back_populates="owner")


class DocumentVisibility(str, PyEnum):
    private = "private"
    public = "public"


class DocumentStatus(str, PyEnum):
    processing = "processing"
    ready = "ready"
    failed = "failed"


class ExtractionJobStatus(str, PyEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class Document(TimestampMixin, Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    original_name: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024))
    mime_type: Mapped[str] = mapped_column(String(255), default="application/pdf")
    source_url: Mapped[str | None] = mapped_column(String(2048), default=None)  # set for URL imports
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"),
        default=DocumentStatus.processing,
        index=True,
    )
    error: Mapped[str | None] = mapped_column(Text, default=None)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0.0)  # 0.0 - 1.0 during processing
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )
    visibility: Mapped[str] = mapped_column(
        String(16),
        default=DocumentVisibility.public.value,
        server_default="public",
        index=True,
    )

    owner: Mapped[User | None] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page: Mapped[int] = mapped_column(Integer, default=1)
    # Human-readable source position ("Page 3", "Slide 2", "Sheet 'X' rows 5-24",
    # "Section: ..."). Null for PDFs ingested before migration 0003.
    location: Mapped[str | None] = mapped_column(String(255), default=None)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str | None] = mapped_column(String(255), default=None)
    # Populated by a generated column added in migration 0002:
    #   tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
    embedding = mapped_column(
        Vector(get_settings().embed_dim), nullable=True, default=None
    )

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(255), default="New conversation")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), default=None, index=True
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(TimestampMixin, Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list | None] = mapped_column(JSON, default=None)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    feedback_rating: Mapped[str | None] = mapped_column(String(8), default=None)  # "up" | "down"
    feedback_comment: Mapped[str | None] = mapped_column(Text, default=None)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class QueryLog(Base):
    """One row per answered question - fuels the /api/stats dashboard."""

    __tablename__ = "query_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), index=True
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )
    question: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(255))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cached: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    retrieval_ms: Mapped[int] = mapped_column(Integer, default=0)
    llm_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_ms: Mapped[int] = mapped_column(Integer, default=0)
    retrieved_chunk_ids: Mapped[list | None] = mapped_column(JSON, default=None)


class RefreshToken(Base):
    """Opaque refresh tokens, stored hashed. Rotated on use; a replayed token
    revokes its whole family (theft detection)."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=_uuid, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class AuditLog(Base):
    """Security-relevant event trail: who did what, to what, from where."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None, index=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), default=None)
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), default=None)
    resource_id: Mapped[str | None] = mapped_column(String(64), default=None)
    ip: Mapped[str | None] = mapped_column(String(64), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(512), default=None)
    detail: Mapped[dict | None] = mapped_column(JSON, default=None)


class ApiKey(TimestampMixin, Base):
    """Programmatic access key (Phase 4 integrations). Only the SHA-256 hash is
    stored — the raw key (dq_...-prefixed) is shown exactly once at creation.
    Scopes: read | chat | upload; JWT sessions implicitly hold every scope."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), default="API key")
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16))  # display form, e.g. dq_ab12cd34
    scopes: Mapped[list | None] = mapped_column(JSON, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Webhook(TimestampMixin, Base):
    """Outgoing webhook subscription (document.ready / document.failed)."""

    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(2048))
    secret: Mapped[str] = mapped_column(String(128))  # HMAC-SHA256 signing key, shown once
    events: Mapped[list | None] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class WebhookDelivery(Base):
    """One row per delivery attempt series for an event, for observability."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), index=True
    )
    webhook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("webhooks.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSON, default=None)
    status: Mapped[str] = mapped_column(String(16))  # "delivered" | "failed"
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response_code: Mapped[int | None] = mapped_column(Integer, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)


class ExtractionJob(TimestampMixin, Base):
    """Structured extraction run over one or more documents (professional+).

    Created by POST /api/extract in async mode; executed on the existing worker
    path. Results carry the extracted data, per-field source citations and the
    model's self-assessed confidence."""

    __tablename__ = "extraction_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )
    status: Mapped[ExtractionJobStatus] = mapped_column(
        Enum(ExtractionJobStatus, name="extraction_job_status"),
        default=ExtractionJobStatus.pending,
        index=True,
    )
    template: Mapped[str] = mapped_column(String(64), default="custom")
    document_ids: Mapped[list | None] = mapped_column(JSON, default=None)
    schema_json: Mapped[dict | None] = mapped_column(JSON, default=None)
    result: Mapped[dict | None] = mapped_column(JSON, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    confidence: Mapped[str | None] = mapped_column(String(16), default=None)


class SemanticCacheEntry(Base):
    """Cached answer for a semantically-repeated question within one tenant scope.
    Invalidated wholesale whenever the document corpus changes."""

    __tablename__ = "semantic_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_hash: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text)
    question_embedding = mapped_column(
        Vector(get_settings().embed_dim), nullable=True, default=None
    )
    answer: Mapped[str] = mapped_column(Text)
    citations: Mapped[list | None] = mapped_column(JSON, default=None)
    methods: Mapped[list | None] = mapped_column(JSON, default=None)
    model: Mapped[str] = mapped_column(String(255), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
