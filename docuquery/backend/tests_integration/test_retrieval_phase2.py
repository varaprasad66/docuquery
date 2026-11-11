"""Phase 2 integration: degraded (keyword-only) ingestion + re-embed, the
low_relevance refusal path, and neighbor-expanded retrieval against real Postgres."""

import tempfile
import unittest
import uuid as uuidlib
from pathlib import Path
from unittest.mock import patch

from . import IntegrationTestCase


class TestDegradedIngestion(IntegrationTestCase):
    async def test_embed_failure_marks_degraded_then_reembed_heals(self):
        register = await self.register("degraded@example.com")
        token = register["access_token"]

        with patch("app.workers.tasks.embed_texts_sync", side_effect=RuntimeError("embedder down")):
            doc = await self.upload_pdf(token)
            await self.process_document(doc["id"])
        detail = await self.wait_ready(doc["id"], token)
        self.assertEqual(detail["status"], "ready")
        self.assertTrue(detail["degraded"])

        # Keyword-only search still finds the document.
        r = await self.client.post(
            "/api/search", json={"query": "annual leave"}, headers=self.auth_headers(token)
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(any(hit["document_id"] == doc["id"] for hit in r.json()["results"]))

        # Re-embed endpoint heals the document (fake embedder is active again).
        r = await self.client.post(
            f"/api/documents/{doc['id']}/reembed", headers=self.auth_headers(token)
        )
        self.assertEqual(r.status_code, 200, r.text)
        detail = await self.wait_ready(doc["id"], token)
        self.assertFalse(detail["degraded"])
        self.assertEqual(detail["status"], "ready")

        # Semantic search works again: vector leg returns the chunk.
        from app.db.session import SessionLocal
        from app.services.retrieval import retrieve

        async with SessionLocal() as db:
            sources, methods = await retrieve(
                db, "annual leave", top_k=3, owner_id=uuidlib.UUID(register["user"]["id"])
            )
        self.assertIn("vector", methods)

    async def test_reembed_rejects_document_not_ready(self):
        register = await self.register("reembed409@example.com")
        token = register["access_token"]
        doc = await self.upload_pdf(token)
        await self.process_document(doc["id"])

        from sqlalchemy import update

        from app.db.models import Document
        from app.db.session import SessionLocal

        async with SessionLocal() as db:
            await db.execute(
                update(Document)
                .where(Document.id == uuidlib.UUID(doc["id"]))
                .values(status="failed")
            )
            await db.commit()

        r = await self.client.post(
            f"/api/documents/{doc['id']}/reembed", headers=self.auth_headers(token)
        )
        self.assertEqual(r.status_code, 409, r.text)


class TestLowRelevanceRefusal(IntegrationTestCase):
    async def test_below_floor_refuses_with_low_relevance_reason(self):
        register = await self.register("floor@example.com")
        token = register["access_token"]
        doc = await self.upload_pdf(token)
        await self.process_document(doc["id"])

        patcher = patch.object(self._settings(), "min_relevance", 0.99)
        patcher.start()
        self.addCleanup(patcher.stop)

        events = await self.chat_sse("What is the annual leave policy?", token)
        done = [e for e in events if e["type"] == "done"][-1]
        self.assertEqual(done["refusal_reason"], "low_relevance")
        self.assertFalse(done.get("unverified", False))
        tokens = "".join(e["value"] for e in events if e["type"] == "token")
        self.assertIn("don't have enough information", tokens)
        self.assertTrue(all(e["type"] != "citations" or not e["citations"] for e in events))

    async def test_zero_floor_keeps_answers(self):
        register = await self.register("flooroff@example.com")
        token = register["access_token"]
        doc = await self.upload_pdf(token)
        await self.process_document(doc["id"])

        patcher = patch.object(self._settings(), "min_relevance", 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)

        events = await self.chat_sse("What is the annual leave policy?", token)
        done = [e for e in events if e["type"] == "done"][-1]
        self.assertEqual(done["refusal_reason"], "ok")

    def _settings(self):
        from app.config import get_settings

        return get_settings()


class TestNeighborExpansion(IntegrationTestCase):
    async def test_retrieve_attaches_adjacent_chunks_as_context(self):
        register = await self.register("neighbor@example.com")
        token = register["access_token"]
        # Four pages -> four chunks with consecutive chunk_index values.
        doc = await self.upload_pdf(token, content=self._four_page_pdf())
        await self.process_document(doc["id"])

        from app.db.session import SessionLocal
        from app.services.retrieval import retrieve

        async with SessionLocal() as db:
            sources, methods = await retrieve(
                db, "annual leave", top_k=2, owner_id=uuidlib.UUID(register["user"]["id"])
            )
        self.assertIn("bm25", methods)
        self.assertTrue(sources)
        expanded = [s for s in sources if s.neighbor_texts]
        self.assertTrue(expanded, "expected at least one hit with neighbor context")
        for source in expanded:
            # Neighbors join the prompt but never the cited chunk text.
            self.assertNotIn(source.neighbor_texts[0], source.content)
            self.assertIn(source.neighbor_texts[0], source.context_text)

    async def test_disabled_flag_keeps_hits_untouched(self):
        register = await self.register("neighboff@example.com")
        token = register["access_token"]
        doc = await self.upload_pdf(token, content=self._four_page_pdf())
        await self.process_document(doc["id"])

        patcher = patch.object(self._settings(), "neighbor_expansion", False)
        patcher.start()
        self.addCleanup(patcher.stop)

        from app.db.session import SessionLocal
        from app.services.retrieval import retrieve

        async with SessionLocal() as db:
            sources, _ = await retrieve(
                db, "annual leave", top_k=2, owner_id=uuidlib.UUID(register["user"]["id"])
            )
        self.assertTrue(all(not s.neighbor_texts for s in sources))

    def _settings(self):
        from app.config import get_settings

        return get_settings()

    def _four_page_pdf(self) -> bytes:
        from . import make_pdf

        return make_pdf(
            pages=4,
            text="Annual leave is 25 days per year with rollover. Sick leave is recorded separately.",
        )


class TestMarkdownExtractor(unittest.TestCase):
    """PDF_EXTRACTOR=markdown: layout-aware extraction without the database."""

    def test_pages_extracted_with_page_locations(self):
        import fitz

        from app.services.parsers import parse_pdf_markdown

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 100), "Annual leave is 25 days per year.")
        tmp = Path(tempfile.gettempdir()) / f"md_extract_{uuidlib.uuid4().hex}.pdf"
        try:
            tmp.write_bytes(doc.tobytes())
            sections = parse_pdf_markdown(tmp)
        finally:
            doc.close()
            tmp.unlink(missing_ok=True)
        self.assertTrue(sections)
        joined = "\n".join(s.text for s in sections)
        self.assertIn("Annual leave", joined)
        for section in sections:
            self.assertTrue(section.location.startswith("Page"), section.location)

    def test_falls_back_to_plain_parser_without_pymupdf4llm(self):
        import builtins
        import fitz

        from app.services.parsers import parse_pdf_markdown

        doc = fitz.open()
        doc.new_page().insert_text((72, 100), "Plain text capture.")
        tmp = Path(tempfile.gettempdir()) / f"md_fallback_{uuidlib.uuid4().hex}.pdf"
        try:
            tmp.write_bytes(doc.tobytes())
            real_import = builtins.__import__

            def no_pymupdf4llm(name, *args, **kwargs):
                if name == "pymupdf4llm":
                    raise ImportError("simulated missing dependency")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=no_pymupdf4llm):
                sections = parse_pdf_markdown(tmp)
            self.assertTrue(sections)
            self.assertIn("Plain text capture.", "\n".join(s.text for s in sections))
        finally:
            doc.close()
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
