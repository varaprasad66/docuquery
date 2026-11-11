"""Embeddings via the OpenAI-compatible provider abstraction."""

import logging
from typing import Literal

import openai

from app.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_EMBED_TIMEOUT_SECONDS = 30.0

EmbedTask = Literal["query", "document"]


def embedding_version() -> str:
    """Version tag stored on chunks.embedding_model. The tag changes whenever
    embed inputs change (task prefixes), so stale vectors are detectable and
    scripts/reindex_embeddings.py can find them."""
    s = get_settings()
    if s.embed_doc_prefix or s.embed_query_prefix:
        return f"{s.embeddings_model}+{s.embedding_version_tag}"
    return s.embeddings_model


def _apply_task_prefix(text: str, task: EmbedTask | None) -> str:
    s = get_settings()
    if task == "query":
        return f"{s.embed_query_prefix}{text}" if s.embed_query_prefix else text
    if task == "document":
        return f"{s.embed_doc_prefix}{text}" if s.embed_doc_prefix else text
    return text


def contextual_embed_input(document_name: str, page: int, location: str | None, content: str) -> str:
    """Text sent to the embedder for one chunk. With CONTEXTUAL_EMBEDDINGS the
    chunk is prefixed with its document name + location (embedding only — the
    stored/displayed content stays clean). Shared by ingestion and reindex."""
    if not get_settings().contextual_embeddings:
        return content
    where = location or f"Page {page}"
    return f"Document: {document_name} | {where}\n{content}"


def embeddings_client() -> openai.AsyncOpenAI:
    s = get_settings()
    return openai.AsyncOpenAI(
        base_url=s.embeddings_base_url,
        api_key=s.embeddings_api_key,
        max_retries=1,
        timeout=DEFAULT_EMBED_TIMEOUT_SECONDS,
    )


def sync_embeddings_client() -> openai.OpenAI:
    s = get_settings()
    return openai.OpenAI(
        base_url=s.embeddings_base_url,
        api_key=s.embeddings_api_key,
        max_retries=1,
        timeout=DEFAULT_EMBED_TIMEOUT_SECONDS,
    )


def _sanitize_texts(texts: list[str]) -> list[str]:
    return [t.replace("\x00", "").strip() or " " for t in texts]


async def embed_texts(
    texts: list[str], task: EmbedTask | None = None
) -> list[list[float]]:
    """Embed a batch of texts. Raises on failure so callers can fall back to BM25-only.

    task: "query" for search questions / answer sentences, "document" for stored
    chunks — applies the model's task prefix (nomic-embed-text trains with them).
    """
    s = get_settings()
    if not texts:
        return []
    cleaned = [_apply_task_prefix(t, task) for t in _sanitize_texts(texts)]
    client = embeddings_client()
    resp = await client.embeddings.create(model=s.embeddings_model, input=cleaned)
    data = sorted(resp.data, key=lambda d: d.index)
    vectors = [d.embedding for d in data]
    for v in vectors:
        if len(v) != s.embed_dim:
            raise ValueError(
                f"Embedding model returned {len(v)} dims but EMBED_DIM={s.embed_dim}. "
                "Update EMBED_DIM in .env to match the model."
            )
    return vectors


def embed_texts_sync(
    texts: list[str], task: EmbedTask | None = None
) -> list[list[float]]:
    s = get_settings()
    if not texts:
        return []
    cleaned = [_apply_task_prefix(t, task) for t in _sanitize_texts(texts)]
    client = sync_embeddings_client()
    resp = client.embeddings.create(model=s.embeddings_model, input=cleaned)
    data = sorted(resp.data, key=lambda d: d.index)
    vectors = [d.embedding for d in data]
    for v in vectors:
        if len(v) != s.embed_dim:
            raise ValueError(
                f"Embedding model returned {len(v)} dims but EMBED_DIM={s.embed_dim}. "
                "Update EMBED_DIM in .env to match the model."
            )
    return vectors
