"""Hybrid retrieval: Postgres full-text (BM25-style) + pgvector semantic search,
fused with reciprocal rank fusion (RRF).

Every query is **tenant-scoped**: callers pass the requester's identity and the SQL
only ever touches documents that identity may read (own + public). This is the core
data-isolation control — see services/security.py::scope_clause.

If the embedding endpoint is unavailable, falls back to BM25-only so the system keeps
working (marked degraded by the caller).
"""

import logging
import re
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.embeddings import embed_texts
from app.services.security import scope_clause

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
    location: str | None = None  # "Page 3", "Slide 2", "Sheet 'X' rows 5-24", ...

    @property
    def source_label(self) -> str:
        return self.location or f"Page {self.page}"

    @property
    def snippet(self) -> str:
        return self.content[:280] + ("..." if len(self.content) > 280 else "")


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


def fuse_rrf(
    vector_hits: list[Source], keyword_hits: list[Source], top_k: int
) -> list[Source]:
    """Reciprocal Rank Fusion over both legs. Pure function — unit tested."""
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
    return sorted(fused.values(), key=lambda s: s.score, reverse=True)[:top_k]


async def retrieve(
    db: AsyncSession,
    query: str,
    top_k: int = 6,
    candidate_multiplier: int = 3,
    owner_id: UUID | None = None,
    document_ids: list[UUID] | None = None,
) -> tuple[list[Source], list[str]]:
    """Returns (fused sources, retrieval methods used).

    owner_id: the requesting user (None = guest -> public documents only).
    document_ids: optional allow-list filter (search-within-documents).
    """
    candidates = top_k * candidate_multiplier
    methods: list[str] = []
    visibility = scope_clause(owner_id)
    binds: dict = {"k": candidates}
    if owner_id is not None:
        binds["owner_id"] = str(owner_id)

    # B608 reviewed: the interpolated fragments are static SQL plus bind
    # placeholders (:doc_i) — user values never enter the query string.
    doc_filter = ""
    if document_ids:
        # Parameterized per-id binds; UUIDs are validated by the API schema.
        id_binds = []
        for i, did in enumerate(document_ids):
            binds[f"doc_{i}"] = str(did)
            id_binds.append(f"CAST(:doc_{i} AS uuid)")
        doc_filter = f" AND c.document_id IN ({', '.join(id_binds)})"

    # --- Semantic leg (pgvector cosine distance) ---
    vector_hits: list[Source] = []
    try:
        query_vector = _vector_literal((await embed_texts([query]))[0])
        binds["qvec"] = query_vector
        vector_sql = f"""
            SELECT c.id, c.document_id, d.original_name, c.page, c.location, c.content,
                   (c.embedding <=> CAST(:qvec AS vector)) AS distance
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE d.status = 'ready' AND c.embedding IS NOT NULL
              AND {visibility}{doc_filter}
            ORDER BY c.embedding <=> CAST(:qvec AS vector)
            LIMIT :k
            """  # nosec B608 - binds-only interpolation, see doc_filter note
        rows = await db.execute(text(vector_sql), binds)
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
                    location=row.location,
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
        binds["q"] = or_query
        keyword_sql = f"""
            SELECT c.id, c.document_id, d.original_name, c.page, c.location, c.content,
                   ts_rank_cd(c.tsv, query) AS rank
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            JOIN to_tsquery('english', :q) query ON c.tsv @@ query
            WHERE d.status = 'ready'
              AND {visibility}{doc_filter}
            ORDER BY rank DESC
            LIMIT :k
            """  # nosec B608 - binds-only interpolation, see doc_filter note
        rows = await db.execute(text(keyword_sql), binds)
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
                    location=row.location,
                )
            )
    if keyword_hits:
        methods.append("bm25")

    # --- Reciprocal rank fusion ---
    # With the optional reranker enabled, fuse the wider candidate pool
    # (top_k * multiplier) and let the cross-encoder pick the final top_k;
    # otherwise RRF's top_k is the final result.
    from app.config import get_settings

    fused_limit = top_k * candidate_multiplier if get_settings().reranker_enabled else top_k
    ranked = fuse_rrf(vector_hits, keyword_hits, fused_limit)
    if get_settings().reranker_enabled and ranked:
        from app.services.reranker import rerank_sources

        ranked = rerank_sources(query, ranked, top_k)
    return ranked, methods
