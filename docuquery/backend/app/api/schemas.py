import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import DocumentStatus


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
    created_at: datetime


class DocumentDetail(DocumentOut):
    stored_path: str


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
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    citations: list[Citation] | None = None
    latency_ms: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    message_count: int = 0

    model_config = {"from_attributes": True}
