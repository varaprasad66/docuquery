from datetime import datetime
from typing import Literal
import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import get_settings
from app.db.models import DocumentStatus


# --- Auth Schemas ---
class UserRegister(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@]+@[^@]+\.[^@]+$")
    password: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("password")
    @classmethod
    def password_policy(cls, v: str) -> str:
        """Minimum length plus at least one letter and one digit."""
        s = get_settings()
        if len(v) < s.password_min_length:
            raise ValueError(f"Password must be at least {s.password_min_length} characters.")
        if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("Password must contain at least one letter and one digit.")
        return v


class UserLogin(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None = None
    role: str = "user"
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    user: UserOut


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=256)


# --- Document Schemas ---
class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_name: str
    status: DocumentStatus
    error: str | None
    page_count: int
    chunk_count: int
    progress: float
    size_bytes: int
    visibility: str = "public"
    owner_id: uuid.UUID | None = None
    created_at: datetime


class DocumentDetail(DocumentOut):
    pass


class VisibilityUpdate(BaseModel):
    visibility: Literal["private", "public"]


class UrlImportRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    visibility: Literal["private", "public"] = "private"


# --- Search Schemas ---
class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=6, ge=1, le=20)
    document_ids: list[uuid.UUID] | None = Field(
        default=None, max_length=25, description="Optional: restrict search to these documents."
    )


class SearchResult(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    page: int
    location: str | None = None
    snippet: str
    score: float
    matched_by: list[str]


class SearchResponse(BaseModel):
    query: str
    methods: list[str]
    results: list[SearchResult]
    took_ms: int


# --- Chat Schemas ---
class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None
    top_k: int = Field(default=6, ge=1, le=12)
    document_ids: list[uuid.UUID] | None = Field(
        default=None, max_length=25, description="Optional: answer only from these documents."
    )


class Citation(BaseModel):
    n: int
    document_id: uuid.UUID
    document_name: str
    page: int
    location: str | None = None  # "Slide 2", "Sheet 'X' rows 5-24", ... for non-PDF formats
    snippet: str


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    citations: list[Citation] | None = None
    latency_ms: int = 0
    feedback_rating: str | None = None
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    message_count: int = 0


class FeedbackRequest(BaseModel):
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=2000)


# --- Stats Schemas ---
class LatencyStats(BaseModel):
    avg_total_ms: float = 0.0
    avg_retrieval_ms: float = 0.0
    avg_llm_ms: float = 0.0


class TokenStats(BaseModel):
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    cost_saved_by_cache_usd: float = 0.0


class DailyUsagePoint(BaseModel):
    date: str
    queries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    avg_total_ms: float = 0.0


class CacheStats(BaseModel):
    enabled: bool = False
    hits: int = 0
    misses: int = 0
    hit_rate_pct: float = 0.0


class FeedbackStats(BaseModel):
    up: int = 0
    down: int = 0


class QueryLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | str
    question: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float = 0.0
    cached: bool = False
    retrieval_ms: int
    llm_ms: int
    total_ms: int
    created_at: datetime


class StatsOverview(BaseModel):
    total_documents: int
    ready_documents: int
    failed_documents: int
    total_chunks: int
    total_pages: int
    total_queries: int
    latency: LatencyStats
    tokens: TokenStats
    recent_queries: list[QueryLogOut]
    cache: CacheStats = CacheStats()
    feedback: FeedbackStats = FeedbackStats()
    daily_usage: list[DailyUsagePoint] = []
    scope: str = "all"  # "user" | "guest" | "global"


# --- Admin Schemas ---
class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None = None
    role: str
    is_active: bool
    created_at: datetime


class AdminUserPatch(BaseModel):
    is_active: bool | None = None
    role: Literal["user", "admin"] | None = None
    display_name: str | None = Field(default=None, max_length=120)


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    actor_id: uuid.UUID | None = None
    actor_email: str | None = None
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    ip: str | None = None
    detail: dict | None = None
