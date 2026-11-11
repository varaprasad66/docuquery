"""Release-blocking privacy, persistence and idempotency regressions."""
import uuid

from sqlalchemy import func, select

from tests_integration import IntegrationTestCase


class TestReleaseRegressions(IntegrationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.account = await self.register(f"release-{uuid.uuid4().hex}@test.io")
        self.token = self.account["access_token"]

    async def test_vector_database_error_recovers_keyword_search(self):
        from unittest.mock import AsyncMock, patch
        doc = await self.upload_pdf(self.token, text="Annual leave is twenty five days.")
        await self.process_document(doc["id"])
        with patch("app.services.retrieval.embed_texts", new=AsyncMock(return_value=[[1.0, 0.0]])):
            response = await self.client.post("/api/search", json={"query": "annual leave"}, headers=self.auth_headers(self.token))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["results"])
        self.assertIn("bm25", response.json()["methods"])

    async def test_concurrent_questions_respect_daily_reservation(self):
        import asyncio
        from app.config import get_settings
        settings = get_settings()
        previous = settings.max_daily_questions
        settings.max_daily_questions = 1
        try:
            responses = await asyncio.gather(*(self.client.post("/api/chat", json={"question": "Annual leave?"}, headers=self.auth_headers(self.token)) for _ in range(2)))
            self.assertEqual(sorted(response.status_code for response in responses), [200, 429])
        finally:
            settings.max_daily_questions = previous

    async def test_concurrent_uploads_respect_daily_reservation(self):
        import asyncio
        from app.config import get_settings
        from tests_integration import make_pdf
        settings = get_settings()
        previous = settings.max_uploads_per_day
        settings.max_uploads_per_day = 1
        try:
            responses = await asyncio.gather(*(self.client.post("/api/documents", files={"file": ("quota.pdf", make_pdf(), "application/pdf")}, headers=self.auth_headers(self.token)) for _ in range(2)))
            self.assertEqual(sorted(response.status_code for response in responses), [201, 429])
        finally:
            settings.max_uploads_per_day = previous

    async def test_invalid_credentials_never_become_guest(self):
        for credential in ("Bearer broken", "Basic invalid", "Bearer", "Bearer a b"):
            with self.subTest(credential=credential):
                response = await self.client.get("/api/documents", headers={"Authorization": credential})
                self.assertEqual(response.status_code, 401)

    async def test_long_unicode_password_rejected_without_server_error(self):
        response = await self.client.post("/api/auth/register", json={"email": "long@test.io", "password": "Aa1" + "界" * 30})
        self.assertEqual(response.status_code, 422)

    async def test_retry_replaces_chunks_and_count_matches(self):
        from app.db.models import Chunk, Document
        from app.db.session import SessionLocal
        doc = await self.upload_pdf(self.token)
        for _ in range(3):
            await self.process_document(doc["id"])
        async with SessionLocal() as db:
            stored = await db.get(Document, uuid.UUID(doc["id"]))
            count = await db.scalar(select(func.count(Chunk.id)).where(Chunk.document_id == stored.id))
            self.assertGreater(count, 0)
            self.assertEqual(count, stored.chunk_count)

    async def test_read_key_cannot_mutate_documents_or_conversations(self):
        doc = await self.upload_pdf(self.token)
        key = await self.client.post("/api/keys", json={"name": "read only", "scopes": ["read"]}, headers=self.auth_headers(self.token))
        headers = self.auth_headers(key.json()["key"])
        for method, suffix, body in (("DELETE", "", None), ("POST", "/retry", None), ("PATCH", "/visibility", {"visibility": "public"})):
            response = await self.client.request(method, f"/api/documents/{doc['id']}{suffix}", headers=headers, json=body)
            self.assertEqual(response.status_code, 403)
        response = await self.client.delete(f"/api/conversations/{uuid.uuid4()}", headers=headers)
        self.assertEqual(response.status_code, 401)

    async def test_source_download_requires_ownership(self):
        doc = await self.upload_pdf(self.token)
        url = f"/api/documents/{doc['id']}/download"
        self.assertEqual((await self.client.get(url)).status_code, 401)
        bob = await self.register(f"other-{uuid.uuid4().hex}@test.io")
        self.assertEqual((await self.client.get(url, headers=self.auth_headers(bob["access_token"]))).status_code, 404)
        response = await self.client.get(url, headers=self.auth_headers(self.token))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))

    async def test_cached_answer_saved_and_delete_invalidates(self):
        doc = await self.upload_pdf(self.token)
        await self.process_document(doc["id"])
        await self.chat_sse("How much annual leave?", token=self.token)
        events = await self.chat_sse("How much annual leave?", token=self.token)
        done = next(event for event in events if event["type"] == "done")
        self.assertTrue(done.get("cached"))
        self.assertTrue(done.get("message_id"))
        meta = next(event for event in events if event["type"] == "meta")
        history = await self.client.get(f"/api/conversations/{meta['conversation_id']}", headers=self.auth_headers(self.token))
        self.assertTrue(any(message["id"] == done["message_id"] for message in history.json()))
        await self.client.delete(f"/api/documents/{doc['id']}", headers=self.auth_headers(self.token))
        events = await self.chat_sse("How much annual leave?", token=self.token)
        self.assertFalse(next(event for event in events if event["type"] == "meta")["cached"])

    async def test_three_concurrent_sessions_are_isolated(self):
        import asyncio
        async def session(index):
            account = await self.register(f"parallel-{index}-{uuid.uuid4().hex}@test.io")
            token = account["access_token"]
            doc = await self.upload_pdf(token, filename=f"private-{index}.pdf", text=f"Private policy {index}: annual leave is 25 days.")
            await self.process_document(doc["id"])
            events = await self.chat_sse("What is the annual leave policy?", token=token)
            citations = next(event for event in events if event["type"] == "citations")["citations"]
            self.assertTrue(citations)
            self.assertTrue(all(citation["document_id"] == doc["id"] for citation in citations))
            self.assertTrue(any(event["type"] == "done" for event in events))
        await asyncio.gather(*(session(index) for index in range(3)))

    async def test_concurrent_ingestion_does_not_duplicate_chunks(self):
        import asyncio
        from app.db.models import Chunk, Document
        from app.db.session import SessionLocal
        doc = await self.upload_pdf(self.token)
        await asyncio.gather(*(self.process_document(doc["id"]) for _ in range(3)))
        async with SessionLocal() as db:
            stored = await db.get(Document, uuid.UUID(doc["id"]))
            count = await db.scalar(select(func.count(Chunk.id)).where(Chunk.document_id == stored.id))
            self.assertGreater(count, 0)
            self.assertEqual(count, stored.chunk_count)

    async def test_public_to_private_revokes_cached_source(self):
        doc = await self.upload_pdf(self.token, visibility="public")
        await self.process_document(doc["id"])
        bob = await self.register(f"cache-reader-{uuid.uuid4().hex}@test.io")
        token = bob["access_token"]
        await self.chat_sse("How much annual leave?", token=token)
        events = await self.chat_sse("How much annual leave?", token=token)
        self.assertTrue(next(event for event in events if event["type"] == "meta")["cached"])
        response = await self.client.patch(f"/api/documents/{doc['id']}/visibility", headers=self.auth_headers(self.token), json={"visibility": "private"})
        self.assertEqual(response.status_code, 200)
        events = await self.chat_sse("How much annual leave?", token=token)
        self.assertFalse(next(event for event in events if event["type"] == "meta")["cached"])
        citations = next(event for event in events if event["type"] == "citations")["citations"]
        self.assertEqual(citations, [])
