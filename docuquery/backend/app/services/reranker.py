"""Optional local cross-encoder reranker.

Behind RERANKER_ENABLED (default off). The model loads lazily on first use and
requires the optional `sentence-transformers` extra. Any failure — missing
dependency, model download problem, inference error — falls back to the fused
RRF order so retrieval never breaks because of reranking.

Scores are sigmoid-normalized to 0..1 so MIN_RELEVANCE has a consistent
meaning across cross-encoder models (ms-marco and bge families both emit raw
logits). A guaranteed slot keeps at least one keyword-only candidate in the
final top_k: cross-encoders can undervalue exact-term matches that BM25 found.
"""

import logging
import math

from app.config import get_settings
from app.services.retrieval import Source

logger = logging.getLogger(__name__)

_model = None  # lazy singleton; CrossEncoder instance once loaded
_load_failed = False

MAX_SNIPPET_CHARS = 2000


def _load_model():
    """Load the cross-encoder on first use; raise if unavailable."""
    global _model, _load_failed
    if _model is not None:
        return _model
    if _load_failed:
        raise RuntimeError("Reranker model previously failed to load.")
    try:
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder(get_settings().reranker_model, max_length=512)
    except Exception as exc:
        _load_failed = True
        raise RuntimeError(f"Reranker model unavailable: {exc}") from exc
    return _model


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def rerank_sources(query: str, sources: list[Source], top_k: int) -> list[Source]:
    """Re-sort fused candidates by cross-encoder score; RRF order on failure.

    Stores the normalized score on each kept source (score + relevance) so the
    relevance floor has something meaningful to compare against.
    """
    if not sources:
        return sources
    try:
        model = _load_model()
        pairs = [(query, s.content[:MAX_SNIPPET_CHARS]) for s in sources]
        raw = [float(x) for x in model.predict(pairs)]
        scores = [_sigmoid(x) for x in raw]
    except Exception as exc:
        logger.warning("Reranker failed (%s); keeping fused RRF order", exc)
        return sources[:top_k]
    order = sorted(range(len(sources)), key=lambda i: scores[i], reverse=True)
    kept_indices = order[:top_k]
    kept = [sources[i] for i in kept_indices]
    # Keyword-only guarantee: BM25 surfaced these without any vector support;
    # never let the cross-encoder drop every one of them from the final page.
    if top_k < len(sources) and not any("bm25" in s.matched_by for s in kept):
        keyword_only = [
            i
            for i in order
            if "bm25" in sources[i].matched_by and "vector" not in sources[i].matched_by
        ]
        if keyword_only:
            kept_indices[-1] = keyword_only[0]
            kept[-1] = sources[keyword_only[0]]
    for index in kept_indices:
        sources[index].score = scores[index]
        sources[index].relevance = scores[index]
    logger.info(
        "reason=rerank candidates=%d kept=%d top_score=%.3f",
        len(sources), len(kept), kept[0].score if kept else 0.0,
    )
    return kept


def reset_model() -> None:
    """Test helper: drop the cached model so the next call reloads."""
    global _model, _load_failed
    _model = None
    _load_failed = False
