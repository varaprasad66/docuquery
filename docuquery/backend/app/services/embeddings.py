"""Embeddings via the same OpenAI-compatible provider abstraction."""

import openai

from app.config import get_settings


def embeddings_client() -> openai.AsyncOpenAI:
    s = get_settings()
    return openai.AsyncOpenAI(
        base_url=s.embeddings_base_url, api_key=s.embeddings_api_key, max_retries=2
    )


def sync_embeddings_client() -> openai.OpenAI:
    s = get_settings()
    return openai.OpenAI(
        base_url=s.embeddings_base_url, api_key=s.embeddings_api_key, max_retries=2
    )


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Raises on failure so callers can fall back to BM25-only."""
    s = get_settings()
    if not texts:
        return []
    client = embeddings_client()
    resp = await client.embeddings.create(model=s.embeddings_model, input=texts)
    data = sorted(resp.data, key=lambda d: d.index)
    vectors = [d.embedding for d in data]
    for v in vectors:
        if len(v) != s.embed_dim:
            raise ValueError(
                f"Embedding model returned {len(v)} dims but EMBED_DIM={s.embed_dim}. "
                "Update EMBED_DIM in .env to match the model."
            )
    return vectors


def embed_texts_sync(texts: list[str]) -> list[list[float]]:
    s = get_settings()
    if not texts:
        return []
    client = sync_embeddings_client()
    resp = client.embeddings.create(model=s.embeddings_model, input=texts)
    data = sorted(resp.data, key=lambda d: d.index)
    vectors = [d.embedding for d in data]
    for v in vectors:
        if len(v) != s.embed_dim:
            raise ValueError(
                f"Embedding model returned {len(v)} dims but EMBED_DIM={s.embed_dim}. "
                "Update EMBED_DIM in .env to match the model."
            )
    return vectors
