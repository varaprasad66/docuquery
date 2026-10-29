from datetime import datetime
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import DocumentStatus


# --- Auth Schemas ---
class UserRegister(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@]+@[^@]+\.[^@]+$")
    password: str = Field(min_length=6, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)


class UserLogin(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None = None
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


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
    owner_id: uuid.UUID | None = None
    created_at: datetime


class DocumentDetail(DocumentOut):
    stored_path: str


# --- Search Schemas ---
class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=6, ge=1, le=20)


class SearchResult(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    page: int
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


class Citation(BaseModel):
    n: int
    document_id: uuid.UUID
    document_name: str
    page: int
    snippet: str


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    citations: list[Citation] | None = None
    latency_ms: int = 0
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    message_count: int = 0


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


class QueryLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | str
    question: str
    model: str
    prompt_tokens: int
    completion_tokens: int
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
