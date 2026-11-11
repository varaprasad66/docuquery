"""Phase 3 integration: enriched citation payloads (chunk_id, offsets, chunk
text), first-appearance renumbering through the SSE stream, and the
no-caching rule for refusals."""

import uuid as uuidlib

from unittest.mock import patch

from . import IntegrationTestCase, _FakeCompletions


class TestCitationPayload(IntegrationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.user = await self.register(f"cite-{uuidlib.uuid4().hex[:8]}@test.io")
        self.token = self.user["access_token"]
        doc = await self.upload_pdf(
            self.token,
            filename="handbook.pdf",
            text="Annual leave is 25 days per year and rolls over up to 10 days.",
        )
        await self.process_document(doc["id"])

    async def test_citations_carry_chunk_id_and_offsets(self):
        events = await self.chat_sse("How many annual leave days do I get?", token=self.token)
        citations = next(e for e in events if e["type"] == "citations")["citations"]
        self.assertTrue(citations)
        first = citations[0]
        self.assertTrue(first.get("chunk_id"))
        self.assertIn("25 days", first.get("chunk_text") or "")
        # The fake verifier quotes the chunk verbatim -> exact offsets.
        self.assertIsNotNone(first.get("quote_start"))
        self.assertIsNotNone(first.get("quote_end"))
        start, end = first["quote_start"], first["quote_end"]
        chunk_text = first["chunk_text"]
        self.assertGreaterEqual(start, 0)
        self.assertLessEqual(end, len(chunk_text))
        self.assertIn(" ".join(chunk_text[start:end].split()).casefold(),
                      " ".join(chunk_text.split()).casefold())

    async def test_persisted_message_roundtrips_rich_citation(self):
        events = await self.chat_sse("How many annual leave days do I get?", token=self.token)
        conv_id = next(e for e in events if e["type"] == "meta")["conversation_id"]
        r = await self.client.get(
            f"/api/conversations/{conv_id}", headers=self.auth_headers(self.token)
        )
        self.assertEqual(r.status_code, 200, r.text)
        assistant = next(m for m in r.json() if m["role"] == "assistant")
        citation = (assistant["citations"] or [])[0]
        self.assertTrue(citation.get("chunk_id"))
        self.assertIsNotNone(citation.get("chunk_text"))

    async def test_renumbering_rewrites_markers_in_order(self):
        doc2 = await self.upload_pdf(
            self.token,
            filename="invoice.pdf",
            text="Invoices must be paid within 30 days of receipt.",
        )
        await self.process_document(doc2["id"])

        original = _FakeCompletions.answer
        _FakeCompletions.answer = "Two facts: payment terms [2] and leave days [1]."
        try:
            events = await self.chat_sse("Summarize both documents.", token=self.token)
        finally:
            _FakeCompletions.answer = original
        answer = "".join(e["value"] for e in events if e["type"] == "token")
        self.assertEqual(answer, "Two facts: payment terms [1] and leave days [2].")
        citations = next(e for e in events if e["type"] == "citations")["citations"]
        self.assertEqual([c["n"] for c in citations], [1, 2])
        # Both documents cited, renumbered 1..2 in the order the answer cites
        # them (retrieval order for an ambiguous query is not deterministic).
        self.assertEqual({c["document_name"] for c in citations}, {"handbook.pdf", "invoice.pdf"})
        self.assertTrue(all(c.get("chunk_id") for c in citations))


class TestRefusalNotCached(IntegrationTestCase):
    async def test_low_relevance_refusal_is_never_cached(self):
        self.user = await self.register(f"nocache-{uuidlib.uuid4().hex[:8]}@test.io")
        self.token = self.user["access_token"]
        doc = await self.upload_pdf(self.token)
        await self.process_document(doc["id"])

        from app.config import get_settings

        patcher = patch.object(get_settings(), "min_relevance", 0.99)
        patcher.start()
        refused = await self.chat_sse("What is the annual leave policy?", token=self.token)
        self.assertEqual(
            next(e for e in refused if e["type"] == "done")["refusal_reason"], "low_relevance"
        )
        patcher.stop()  # floor off again for the success-path comparison

        from sqlalchemy import select, func

        from app.db.models import SemanticCacheEntry
        from app.db.session import SessionLocal

        async def cache_count() -> int:
            async with SessionLocal() as db:
                return (await db.execute(select(func.count(SemanticCacheEntry.id)))).scalar_one()

        self.assertEqual(await cache_count(), 0, "refusals must never be written to the answer cache")

        # The same successful question IS cached (ok answers only).
        events = await self.chat_sse("What is the annual leave policy?", token=self.token)
        self.assertFalse(next(e for e in events if e["type"] == "meta")["cached"])
        self.assertEqual(
            next(e for e in events if e["type"] == "done")["refusal_reason"], "ok"
        )
        self.assertEqual(await cache_count(), 1)
