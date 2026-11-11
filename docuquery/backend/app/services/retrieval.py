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

from app.config import get_settings
from app.services.embeddings import embed_texts
from app.services.security import scope_clause

logger = logging.getLogger(__name__)

RRF_K = 60

# Question words that pollute the OR-keyword leg: they match nearly every
# chunk, drowning the rank signal. Dropped before building the tsquery.
STOP_WORDS = frozenset(
    """a about an and are as at be but by can did do does for from had has have
    how i in is it its me of on or that the their them then there these they
    this to was we were what when where which who why will with you your""".split()
)


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
    # Best primitive score (vector cosine similarity / keyword ts_rank_cd) kept
    # through fusion — the RRF score stored in `score` is a rank statistic, not
    # a similarity, and must not be compared against a relevance floor.
    relevance: float = 0.0
    chunk_index: int = 0
    # Neighbor-expansion context: previous/next chunk contents of the same
    # document, embedded in the prompt but NOT part of the cited chunk text.
    neighbor_texts: list[str] = field(default_factory=list)

    @property
    def source_label(self) -> str:
        return self.location or f"Page {self.page}"

    @property
    def snippet(self) -> str:
        return self.content[:280] + ("..." if len(self.content) > 280 else "")

    @property
    def context_text(self) -> str:
        """What the model actually sees for this excerpt: the cited chunk plus
        any attached neighbor chunks."""
        if not self.neighbor_texts:
            return self.content
        return "\n\n".join([self.content, *self.neighbor_texts])


def build_keyword_query(query: str, websearch: bool = True) -> str:
    """Sanitized OR tsquery input: lowercase alphanumeric terms minus stop words.
    websearch=True joins with " or " for websearch_to_tsquery; False joins with
    " | " for to_tsquery. Terms are restricted to [a-z0-9] so neither variant
    can raise a syntax error, and the caller still binds the value."""
    terms = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) > 1]
    terms = [t for t in terms if t not in STOP_WORDS] or terms
    return (" or " if websearch else " | ").join(terms)


def merge_neighbors(
    ranked: list[Source],
    neighbor_map: dict[UUID, list[Source]],
    max_added_chars: int,
) -> int:
    """Attach pre-fetched neighbor chunks to each hit, deduplicated across all
    hits and capped by a total character budget. Pure function — unit tested.
    Returns the number of neighbor texts attached."""
    seen = {s.chunk_id for s in ranked}
    added = 0
    for hit in ranked:
        for neighbor in neighbor_map.get(hit.chunk_id, []):
            if neighbor.chunk_id in seen or added + len(neighbor.content) > max_added_chars:
                continue
            hit.neighbor_texts.append(neighbor.content)
            seen.add(neighbor.chunk_id)
            added += len(neighbor.content)
    return added


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


def fuse_rrf(
    vector_hits: list[Source], keyword_hits: list[Source], top_k: int
) -> list[Source]:
    """Reciprocal Rank Fusion over both legs. Pure function — unit tested.
    `relevance` keeps the strongest primitive score a source ever had."""
    fused: dict[UUID, Source] = {}
    for hits in (vector_hits, keyword_hits):
        for rank, source in enumerate(hits, start=1):
            if source.chunk_id in fused:
                fused[source.chunk_id].score += 1.0 / (RRF_K + rank)
                if source.relevance > fused[source.chunk_id].relevance:
                    fused[source.chunk_id].relevance = source.relevance
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
        query_vector = _vector_literal((await embed_texts([query], task="query"))[0])
        binds["qvec"] = query_vector
        vector_sql = f"""
            SELECT c.id, c.document_id, d.original_name, c.page, c.location, c.content,
                   c.chunk_index, (c.embedding <=> CAST(:qvec AS vector)) AS distance
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE d.status = 'ready' AND c.embedding IS NOT NULL
              AND {visibility}{doc_filter}
            ORDER BY c.embedding <=> CAST(:qvec AS vector)
            LIMIT :k
            """  # nosec B608 - binds-only interpolation, see doc_filter note
        async with db.begin_nested():
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
                    relevance=1.0 - float(row.distance),
                    matched_by=["vector"],
                    location=row.location,
                    chunk_index=row.chunk_index,
                )
            )
        methods.append("vector")
    except Exception:
        logger.exception("Vector retrieval unavailable - falling back to BM25-only")

    # --- Keyword leg (Postgres full-text, BM25-style ranking) ---
    # OR semantics: natural-language questions contain words the doc may not
    # have, so AND-matching (plainto_tsquery) over-filters. Ranking is left to
    # ts_rank_cd + RRF fusion. websearch_to_tsquery never raises on odd input;
    # terms are pre-sanitized and stop words dropped so they can't flood the OR.
    keyword_hits: list[Source] = []
    settings = get_settings()
    or_query = build_keyword_query(query, websearch=settings.keyword_websearch_tsquery)
    if or_query:
        binds["q"] = or_query
        tsquery_fn = (
            "websearch_to_tsquery('english', :q)"
            if settings.keyword_websearch_tsquery
            else "to_tsquery('english', :q)"
        )
        keyword_sql = f"""
            SELECT c.id, c.document_id, d.original_name, c.page, c.location, c.content,
                   c.chunk_index, ts_rank_cd(c.tsv, query) AS rank
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            JOIN {tsquery_fn} query ON c.tsv @@ query
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
                    relevance=float(row.rank),
                    matched_by=["bm25"],
                    location=row.location,
                    chunk_index=row.chunk_index,
                )
            )
    if keyword_hits:
        methods.append("bm25")

    # --- Reciprocal rank fusion ---
    # With the optional reranker enabled, fuse a wide candidate pool and let the
    # cross-encoder pick the final top_k; otherwise RRF's top_k is the result.
    fused_limit = (
        max(top_k * candidate_multiplier, settings.reranker_candidates)
        if settings.reranker_enabled
        else top_k
    )
    ranked = fuse_rrf(vector_hits, keyword_hits, fused_limit)
    if settings.reranker_enabled and ranked:
        from app.services.reranker import rerank_sources

        ranked = rerank_sources(query, ranked, top_k)
    if settings.neighbor_expansion and settings.neighbor_window > 0 and ranked:
        await _attach_neighbors(db, ranked, settings)
    return ranked, methods


async def _attach_neighbors(db: AsyncSession, ranked: list[Source], settings) -> None:
    """Fetch each hit's previous/next chunks (same document, by chunk_index) and
    attach them as uncited context, deduplicated and capped by a character
    budget derived from LLM_CONTEXT_BUDGET_TOKENS (~4 chars/token). The prompt
    budget in services/prompting.py drops overflow if the prompt still runs long.
    Failures are logged and ignored — neighbors are an enhancement, never a
    requirement."""
    window = settings.neighbor_window
    wanted: set[tuple[UUID, int]] = set()
    for source in ranked:
        for delta in range(1, window + 1):
            if source.chunk_index - delta >= 0:
                wanted.add((source.document_id, source.chunk_index - delta))
            wanted.add((source.document_id, source.chunk_index + delta))
    if not wanted:
        return
    try:
        doc_binds = []
        binds: dict = {}
        for i, (document_id, chunk_index) in enumerate(sorted(wanted, key=str)):
            binds[f"d_{i}"] = str(document_id)
            binds[f"i_{i}"] = chunk_index
            doc_binds.append(f"(CAST(:d_{i} AS uuid), CAST(:i_{i} AS int))")
        neighbor_sql = f"""
            SELECT c.id, c.document_id, c.page, c.location, c.content, c.chunk_index
            FROM chunks c
            WHERE (c.document_id, c.chunk_index) IN ({", ".join(doc_binds)})
        """  # nosec B608 - binds-only pairs, no user data in the string
        rows = await db.execute(text(neighbor_sql), binds)
        by_key: dict[tuple[UUID, int], Source] = {}
        for row in rows:
            by_key[(row.document_id, row.chunk_index)] = Source(
                chunk_id=row.id,
                document_id=row.document_id,
                document_name="",
                page=row.page,
                content=row.content,
                location=row.location,
                chunk_index=row.chunk_index,
                matched_by=["neighbor"],
            )
        neighbor_map: dict[UUID, list[Source]] = {}
        for source in ranked:
            for delta in range(1, window + 1):
                for index in (source.chunk_index - delta, source.chunk_index + delta):
                    neighbor = by_key.get((source.document_id, index))
                    if neighbor is not None and neighbor.chunk_id != source.chunk_id:
                        neighbor_map.setdefault(source.chunk_id, []).append(neighbor)
        added = merge_neighbors(
            ranked, neighbor_map, max_added_chars=settings.llm_context_budget_tokens * 2
        )
        if added:
            logger.info(
                "reason=neighbor_expansion hits=%d added=%d chars=%d",
                len(ranked), sum(len(s.neighbor_texts) for s in ranked), added,
            )
    except Exception:
        logger.warning("Neighbor expansion skipped", exc_info=True)
