"""Semantic answer cache: serve repeated questions without re-paying the LLM.

A cache hit requires: same tenant scope, cosine similarity above threshold, and an
unexpired entry. The cache is invalidated wholesale whenever any document finishes
processing, fails, is re-processed, or is deleted — stale answers can never be served.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import SemanticCacheEntry

logger = logging.getLogger(__name__)


@dataclass
class CacheHit:
    answer: str
    citations: list
    methods: list
    prompt_tokens: int
    completion_tokens: int


def scope_hash(owner_id: UUID | None, document_ids: list[str] | None = None) -> str:
    """Tenant scope fingerprint. Guest scope is distinct from any user scope."""
    raw = f"owner:{owner_id or 'guest'}|docs:{sorted(document_ids or [])}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


async def lookup(
    db: AsyncSession,
    owner_id: UUID | None,
    query_vector_literal: str,
    question: str,
    document_ids: list[str] | None = None,
) -> CacheHit | None:
    s = get_settings()
    if not s.cache_enabled:
        return None
    threshold_distance = 1.0 - s.cache_similarity_threshold
    try:
        rows = await db.execute(
            text(
                """
                SELECT id, question, answer, citations, methods, prompt_tokens,
                       completion_tokens, (question_embedding <=> CAST(:qvec AS vector)) AS dist
                FROM semantic_cache
                WHERE scope_hash = :scope AND expires_at > now()
                  AND question_embedding IS NOT NULL
                ORDER BY question_embedding <=> CAST(:qvec AS vector)
                LIMIT 1
                """
            ),
            {
                "qvec": query_vector_literal,
                "scope": scope_hash(owner_id, document_ids),
            },
        )
        row = rows.first()
        if row is None or float(row.dist) > threshold_distance:
            return None
        await db.execute(
            text("UPDATE semantic_cache SET hit_count = hit_count + 1 WHERE id = :id"),
            {"id": row.id},
        )
        await db.commit()
        return CacheHit(
            answer=row.answer,
            citations=list(row.citations or []),
            methods=list(row.methods or []),
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
        )
    except Exception:
        logger.warning("Semantic cache lookup failed; bypassing cache", exc_info=True)
        return None


async def store(
    db: AsyncSession,
    owner_id: UUID | None,
    question: str,
    question_embedding_literal: str,
    answer: str,
    citations: list,
    methods: list,
    prompt_tokens: int,
    completion_tokens: int,
    document_ids: list[str] | None = None,
) -> None:
    s = get_settings()
    if not s.cache_enabled:
        return
    try:
        await db.execute(
            text(
                """
                INSERT INTO semantic_cache
                    (scope_hash, question, question_embedding, answer, citations, methods,
                     model, prompt_tokens, completion_tokens, hit_count, created_at, expires_at)
                VALUES (:scope, :question, CAST(:qvec AS vector), :answer, :citations, :methods,
                     :model, :ptok, :ctok, 0, now(), :expires)
                """
            ),
            {
                "scope": scope_hash(owner_id, document_ids),
                "question": question[:2000],
                "qvec": question_embedding_literal,
                "answer": answer,
                "citations": json.dumps(citations),
                "methods": json.dumps(methods),
                "model": s.llm_model,
                "ptok": prompt_tokens,
                "ctok": completion_tokens,
                "expires": datetime.now(timezone.utc)
                + timedelta(minutes=s.cache_ttl_minutes),
            },
        )
        await db.commit()
    except Exception:
        logger.warning("Semantic cache store failed", exc_info=True)


async def invalidate_all(db: AsyncSession) -> None:
    """Called whenever the document corpus changes: any document becomes ready/failed,
    is deleted, or is re-processed."""
    try:
        await db.execute(delete(SemanticCacheEntry))
        await db.commit()
    except Exception:
        logger.warning("Semantic cache invalidation failed", exc_info=True)
        await db.rollback()
