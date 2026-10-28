"""Hybrid retrieval: Postgres full-text (BM25-style) + pgvector semantic search,
fused with reciprocal rank fusion (RRF).

If the embedding endpoint is unavailable, falls back to BM25-only so the
system keeps working (marked degraded by the caller).
"""

import logging
import re
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.embeddings import embed_texts

logger = logging.getLogger(__name__)

RRF_K = 60


@dataclass
class Source:
    chunk_id: UUID
    document_id: UUID
    document_name: str
    page: int
    content: str
    score: float = 0.0
    matched_by: list[str] = field(default_factory=list)

    @property
    def snippet(self) -> str:
        return self.content[:280] + ("..." if len(self.content) > 280 else "")


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


async def retrieve(
    db: AsyncSession, query: str, top_k: int = 6, candidate_multiplier: int = 3
) -> tuple[list[Source], list[str]]:
    """Returns (fused sources, retrieval methods used)."""
    candidates = top_k * candidate_multiplier
    methods: list[str] = []

    # --- Semantic leg (pgvector cosine distance) ---
    vector_hits: list[Source] = []
    try:
        query_vector = _vector_literal((await embed_texts([query]))[0])
        rows = await db.execute(
            text(
                """
                SELECT c.id, c.document_id, d.original_name, c.page, c.content,
                       (c.embedding <=> CAST(:qvec AS vector)) AS distance
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE d.status = 'ready' AND c.embedding IS NOT NULL
                ORDER BY c.embedding <=> CAST(:qvec AS vector)
                LIMIT :k
                """
            ),
            {"qvec": query_vector, "k": candidates},
        )
        for row in rows:
            vector_hits.append(
                Source(
                    chunk_id=row.id,
                    document_id=row.document_id,
                    document_name=row.original_name,
                    page=row.page,
                    content=row.content,
                    score=1.0 - float(row.distance),
                    matched_by=["vector"],
                )
            )
        methods.append("vector")
    except Exception:
        logger.exception("Vector retrieval unavailable - falling back to BM25-only")

    # --- Keyword leg (Postgres full-text, BM25-style ranking) ---
    # OR semantics: natural-language questions contain words the doc may not
    # have, so AND-matching (plainto_tsquery) over-filters. Ranking is left to
    # ts_rank_cd + RRF fusion.
    keyword_hits: list[Source] = []
    sanitized_terms = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) > 1]
    if sanitized_terms:
        or_query = " | ".join(sanitized_terms)
        rows = await db.execute(
            text(
                """
                SELECT c.id, c.document_id, d.original_name, c.page, c.content,
                       ts_rank_cd(c.tsv, query) AS rank
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                JOIN to_tsquery('english', :q) query ON c.tsv @@ query
                WHERE d.status = 'ready'
                ORDER BY rank DESC
                LIMIT :k
                """
            ),
            {"q": or_query, "k": candidates},
        )
        for row in rows:
            keyword_hits.append(
                Source(
                    chunk_id=row.id,
                    document_id=row.document_id,
                    document_name=row.original_name,
                    page=row.page,
                    content=row.content,
                    score=float(row.rank),
                    matched_by=["bm25"],
                )
            )
    if keyword_hits:
        methods.append("bm25")

    # --- Reciprocal rank fusion ---
    fused: dict[UUID, Source] = {}
    for hits in (vector_hits, keyword_hits):
        for rank, source in enumerate(hits, start=1):
            if source.chunk_id in fused:
                fused[source.chunk_id].score += 1.0 / (RRF_K + rank)
                for m in source.matched_by:
                    if m not in fused[source.chunk_id].matched_by:
                        fused[source.chunk_id].matched_by.append(m)
            else:
                source.score = 1.0 / (RRF_K + rank)
                fused[source.chunk_id] = source

    ranked = sorted(fused.values(), key=lambda s: s.score, reverse=True)
    return ranked[:top_k], methods
