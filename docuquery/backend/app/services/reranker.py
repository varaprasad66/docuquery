"""Optional local cross-encoder reranker.

Behind RERANKER_ENABLED (default off). The model loads lazily on first use and
requires the optional `sentence-transformers` extra. Any failure — missing
dependency, model download problem, inference error — falls back to the fused
RRF order so retrieval never breaks because of reranking.
"""

import logging

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


def rerank_sources(query: str, sources: list[Source], top_k: int) -> list[Source]:
    """Re-sort fused candidates by cross-encoder score; RRF order on failure."""
    if not sources:
        return sources
    try:
        model = _load_model()
        pairs = [(query, s.content[:MAX_SNIPPET_CHARS]) for s in sources]
        scores = model.predict(pairs)
        order = sorted(range(len(sources)), key=lambda i: float(scores[i]), reverse=True)
        return [sources[i] for i in order[:top_k]]
    except Exception as exc:
        logger.warning("Reranker failed (%s); keeping fused RRF order", exc)
        return sources[:top_k]


def reset_model() -> None:
    """Test helper: drop the cached model so the next call reloads."""
    global _model, _load_failed
    _model = None
    _load_failed = False
