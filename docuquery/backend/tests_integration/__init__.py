"""Integration test package.

Importing this package (in a FRESH python process, before any app module) points the
application at a disposable test database and provides the shared harness:
schema bootstrap, deterministic fake embeddings/LLM, HTTP client, and memory-isolated
rate limiting backends.

Run via scripts/run_tests.py (phase 2) or:
    python -m unittest discover -s tests_integration -t . -v
"""

import os

import asyncio
import sys
import tempfile
from pathlib import Path

TEST_DB_NAME = "docuquery_test"

# Psycopg async requires the selector event loop on Windows. Must be set before
# any test runner creates its event loops.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Test uploads must NEVER land in the real uploads/ folder. Set before any app
# module imports config (pydantic-settings reads UPLOAD_DIR from the environment).
_TEST_UPLOAD_DIR = Path(tempfile.gettempdir()) / "docuquery_test_uploads"
os.environ["UPLOAD_DIR"] = str(_TEST_UPLOAD_DIR)

TEST_DATABASE_URL = (
    f"postgresql+psycopg://docuquery:docuquery@localhost:5433/{TEST_DB_NAME}"
)
SERVER_DATABASE_URL = "postgresql+psycopg://docuquery:docuquery@localhost:5433/docuquery"

# Must be set before ANY app module is imported (config caches the URL).
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ENVIRONMENT"] = "local"
os.environ["EDITION"] = "enterprise"
os.environ["CACHE_ENABLED"] = "True"

import hashlib  # noqa: E402
import io  # noqa: E402
import math  # noqa: E402
import unittest  # noqa: E402

EMBED_DIM = 768


# --------------------------------------------------------------------------- #
# Schema bootstrap (runs once per process)
# --------------------------------------------------------------------------- #
def _ensure_database() -> None:
    import psycopg

    server_url = SERVER_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    conn = psycopg.connect(server_url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True)
    try:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        conn.close()


def _ensure_schema() -> None:
    from sqlalchemy import create_engine, text

    from app.db.models import Base

    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as conn:
        # pgvector type must exist before any table with VECTOR columns is created.
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        # Generated full-text column + indexes (mirrors migration 0001 extras)
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='chunks' AND column_name='tsv'
                    ) THEN
                        ALTER TABLE chunks ADD COLUMN tsv tsvector
                        GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
                    END IF;
                END $$;
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_chunks_tsv ON chunks USING gin (tsv)"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_chunks_embedding ON chunks USING hnsw "
                "(embedding vector_cosine_ops)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_semantic_cache_embedding ON semantic_cache "
                "USING hnsw (question_embedding vector_cosine_ops)"
            )
        )
        # Self-healing for columns added after this test database was first created
        # (create_all does not ALTER existing tables).
        conn.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS location VARCHAR(255)"))
        conn.execute(
            text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_url VARCHAR(2048)")
        )
    engine.dispose()


try:
    _ensure_database()
    _ensure_schema()
    SCHEMA_READY = True
except Exception as _exc:  # pragma: no cover - environment without Postgres
    SCHEMA_READY = False
    _BOOTSTRAP_ERROR = str(_exc)


# --------------------------------------------------------------------------- #
# Deterministic fakes
# --------------------------------------------------------------------------- #
def _fake_vector(text: str) -> list[float]:
    """Deterministic pseudo-embedding: identical text -> identical vector."""
    digest = hashlib.sha256(text.encode()).digest()
    vec = [0.0] * EMBED_DIM
    for i in range(EMBED_DIM):
        byte = digest[i % len(digest)]
        vec[i] = ((byte * (i + 3)) % 256 - 128) / 128.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def fake_embed_texts(texts: list[str]) -> list[list[float]]:
    return [_fake_vector(t) for t in texts]


def fake_embed_texts_sync(texts: list[str]) -> list[list[float]]:
    return [_fake_vector(t) for t in texts]


class _FakeCompletions:
    """Async streaming chat completions returning a fixed answer with usage."""

    answer = "The handbook states 25 days of PTO per year [1]."

    async def create(self, **kwargs):
        class _Completion:
            def __init__(self, outer):
                self.outer = outer
                self.usage = None
                self.choices = []
                self._sent = 0

        obj = _Completion(self)

        async def _gen():
            usage_sent = False
            for word in _FakeCompletions.answer.split(" "):
                chunk = type("C", (), {})()
                chunk.usage = None
                choice = type("C", (), {})()
                delta = type("C", (), {})()
                delta.content = word + " "
                chunk.choices = [choice]
                choice.delta = delta
                yield chunk
            chunk = type("C", (), {})()
            usage = type("U", (), {})()
            usage.prompt_tokens = 120
            usage.completion_tokens = 18
            chunk.usage = usage
            chunk.choices = []
            yield chunk

        return _gen()


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeLLMClient:
    def __init__(self):
        self.chat = _FakeChat()


def make_pdf(pages: int = 1, text: str = "Annual leave is 25 days per year with rollover.") -> bytes:
    """Build a real digital PDF in memory with known text."""
    import fitz

    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"{text} Page {i + 1}.")
    data = doc.tobytes()
    doc.close()
    return data


def make_encrypted_pdf() -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Secret content.")
    buf = io.BytesIO()
    doc.save(buf, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="ownerpw", user_pw="userpw")
    doc.close()
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Isolation helpers + base test case
# --------------------------------------------------------------------------- #
def force_memory_backends() -> None:
    """Make rate limiting / blacklists deterministic in-process (no Redis)."""
    import app.api.auth as auth_api
    import app.api.dependencies as deps
    import app.services.rate_limit as rl
    import app.services.token_store as ts

    rl._redis_client = lambda: None
    ts._redis_client = lambda: None
    for limiter in (
        deps.rate_limit_chat.limiter,
        deps.rate_limit_upload.limiter,
        deps.rate_limit_search.limiter,
        deps.rate_limit_auth.limiter,
    ):
        limiter._redis = None
        limiter._redis_checked = True
        limiter._memory.clear()
    auth_api.lockout._get_redis = lambda: None
    auth_api.lockout._memory.clear()


def patch_ai_fakes() -> None:
    import app.api.chat as chat_api
    import app.workers.tasks as tasks

    chat_api.llm_client = lambda: _FakeLLMClient()
    tasks.embed_texts_sync = fake_embed_texts_sync


def patch_embeds_everywhere() -> None:
    import app.api.chat as chat_api
    import app.services.embeddings as emb
    import app.services.retrieval as retrieval

    async def _async_embed(texts):
        return fake_embed_texts(texts)

    chat_api.embed_texts = _async_embed
    retrieval.embed_texts = _async_embed
    emb.embed_texts = _async_embed
    emb.embed_texts_sync = fake_embed_texts_sync


class IntegrationTestCase(unittest.IsolatedAsyncioTestCase):
    """Base: fresh HTTP client per test against the real app + test DB."""

    async def asyncSetUp(self):
        if not SCHEMA_READY:
            self.skipTest(f"Test database unavailable: {_BOOTSTRAP_ERROR}")
        import httpx
        from sqlalchemy import text

        from app.db.session import engine

        force_memory_backends()
        patch_ai_fakes()
        patch_embeds_everywhere()

        # Clean slate per test (order matters for FKs).
        async with engine.begin() as conn:
            for table in (
                "semantic_cache",
                "audit_logs",
                "refresh_tokens",
                "query_logs",
                "messages",
                "conversations",
                "chunks",
                "documents",
                "users",
            ):
                await conn.execute(text(f"DELETE FROM {table}"))

        from app.main import app

        self.app = app
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    # -- helpers --
    async def register(self, email: str, password: str = "Password12345") -> dict:
        r = await self.client.post(
            "/api/auth/register", json={"email": email, "password": password}
        )
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    async def login(self, email: str, password: str = "Password12345") -> dict:
        r = await self.client.post("/api/auth/login", json={"email": email, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def auth_headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    async def upload_pdf(
        self,
        token: str | None,
        filename: str = "test.pdf",
        content: bytes | None = None,
        visibility: str = "private",
        text: str = "Annual leave is 25 days per year with rollover.",
    ) -> dict:
        data = content if content is not None else make_pdf(text=text)
        files = {"file": (filename, io.BytesIO(data), "application/pdf")}
        r = await self.client.post(
            f"/api/documents?visibility={visibility}", files=files,
            headers=self.auth_headers(token) if token else {},
        )
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    async def process_document(self, doc_id: str) -> None:
        """Run the ingestion pipeline synchronously (fake embeddings)."""
        import asyncio

        from app.workers.tasks import process_document_core

        await asyncio.to_thread(process_document_core, doc_id)

    async def wait_ready(self, doc_id: str, token: str | None = None) -> dict:
        r = await self.client.get(
            f"/api/documents/{doc_id}", headers=self.auth_headers(token) if token else {}
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    async def chat_sse(self, question: str, token: str | None = None, **kwargs) -> list[dict]:
        """POST /api/chat and parse the SSE event stream into a list of events."""
        payload = {"question": question, **kwargs}
        r = await self.client.post(
            "/api/chat", json=payload, headers=self.auth_headers(token) if token else {}
        )
        self.assertEqual(r.status_code, 200, r.text)
        events = []
        for line in r.text.splitlines():
            if line.startswith("data: "):
                import json

                events.append(json.loads(line[len("data: "):]))
        return events
