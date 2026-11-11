"""TENANT ISOLATION — the regression suite for data leakage between tenants.

These tests assert that user A's private documents can never surface for user B or
for a guest: not in chat citations, not in search results, not in document listings,
not in stats, and never deletable. This suite fails on pre-hardening code.
"""

import uuid

from tests_integration import IntegrationTestCase, make_pdf


class TestTenantIsolation(IntegrationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.alice_email = f"alice-{uuid.uuid4().hex[:8]}@test.io"
        self.bob_email = f"bob-{uuid.uuid4().hex[:8]}@test.io"
        self.alice = await self.register(self.alice_email)
        self.bob = await self.register(self.bob_email)
        self.alice_token = self.alice["access_token"]
        self.bob_token = self.bob["access_token"]

        # Alice uploads a private document and waits for it to index.
        doc = await self.upload_pdf(
            self.alice_token,
            filename="alice-private-salary.pdf",
            text="Alice's confidential salary is 250000 dollars.",
        )
        await self.process_document(doc["id"])
        self.alice_doc = await self.wait_ready(doc["id"], self.alice_token)
        self.assertEqual(self.alice_doc["status"], "ready")

    async def test_search_never_returns_other_users_private_chunks(self):
        r = await self.client.post(
            "/api/search",
            json={"query": "Alice confidential salary 250000"},
            headers=self.auth_headers(self.bob_token),
        )
        self.assertEqual(r.status_code, 200)
        names = [x["document_name"] for x in r.json()["results"]]
        self.assertNotIn("alice-private-salary.pdf", names)

    async def test_guest_search_never_returns_private_chunks(self):
        r = await self.client.post("/api/search", json={"query": "Alice confidential salary"})
        self.assertEqual(r.status_code, 200)
        names = [x["document_name"] for x in r.json()["results"]]
        self.assertNotIn("alice-private-salary.pdf", names)

    async def test_chat_citations_never_include_other_users_documents(self):
        events = await self.chat_sse(
            "What is Alice's confidential salary?", token=self.bob_token
        )
        citations = next(e for e in events if e["type"] == "citations")
        for c in citations["citations"]:
            self.assertNotEqual(c["document_name"], "alice-private-salary.pdf")

    async def test_owner_chat_does_surface_own_document(self):
        events = await self.chat_sse(
            "What is Alice's confidential salary?", token=self.alice_token
        )
        citations = next(e for e in events if e["type"] == "citations")
        self.assertTrue(
            any(c["document_name"] == "alice-private-salary.pdf" for c in citations["citations"]),
            "Owner must retrieve their own document",
        )

    async def test_document_listing_hides_private_documents(self):
        r = await self.client.get("/api/documents", headers=self.auth_headers(self.bob_token))
        names = [d["original_name"] for d in r.json()]
        self.assertNotIn("alice-private-salary.pdf", names)

        # Owner sees their own.
        r = await self.client.get("/api/documents", headers=self.auth_headers(self.alice_token))
        names = [d["original_name"] for d in r.json()]
        self.assertIn("alice-private-salary.pdf", names)

    async def test_private_document_detail_hidden_and_404_shaped(self):
        doc_id = self.alice_doc["id"]
        r = await self.client.get(f"/api/documents/{doc_id}", headers=self.auth_headers(self.bob_token))
        self.assertEqual(r.status_code, 404)  # not 403: existence is not leaked

    async def test_private_document_not_deletable_by_others(self):
        doc_id = self.alice_doc["id"]
        r = await self.client.delete(f"/api/documents/{doc_id}", headers=self.auth_headers(self.bob_token))
        self.assertEqual(r.status_code, 404)
        r = await self.client.get(f"/api/documents/{doc_id}", headers=self.auth_headers(self.alice_token))
        self.assertEqual(r.status_code, 200, "Document must survive the cross-user delete attempt")

    async def test_guest_cannot_delete_or_retry_anything(self):
        doc_id = self.alice_doc["id"]
        r = await self.client.delete(f"/api/documents/{doc_id}")
        self.assertEqual(r.status_code, 401)
        r = await self.client.post(f"/api/documents/{doc_id}/retry")
        self.assertEqual(r.status_code, 401)

    async def test_public_label_does_not_expose_document_in_strict_mode(self):
        doc_id = self.alice_doc["id"]
        r = await self.client.patch(
            f"/api/documents/{doc_id}/visibility",
            json={"visibility": "public"},
            headers=self.auth_headers(self.alice_token),
        )
        self.assertEqual(r.status_code, 200)
        r = await self.client.post(
            "/api/search",
            json={"query": "Alice confidential salary 250000"},
            headers=self.auth_headers(self.bob_token),
        )
        names = [x["document_name"] for x in r.json()["results"]]
        self.assertNotIn("alice-private-salary.pdf", names)

    async def test_visibility_change_requires_owner(self):
        doc_id = self.alice_doc["id"]
        r = await self.client.patch(
            f"/api/documents/{doc_id}/visibility",
            json={"visibility": "public"},
            headers=self.auth_headers(self.bob_token),
        )
        self.assertEqual(r.status_code, 404)

    async def test_guest_upload_rejected(self):
        r = await self.client.post("/api/documents", files={"file": ("guest.pdf", make_pdf(), "application/pdf")})
        self.assertEqual(r.status_code, 401)

    async def test_conversations_are_scoped_per_user(self):
        events = await self.chat_sse("Hello there", token=self.alice_token)
        conv_id = next(e for e in events if e["type"] == "meta")["conversation_id"]

        # Bob cannot see Alice's conversation.
        r = await self.client.get(
            f"/api/conversations/{conv_id}", headers=self.auth_headers(self.bob_token)
        )
        self.assertEqual(r.status_code, 404)
        r = await self.client.delete(
            f"/api/conversations/{conv_id}", headers=self.auth_headers(self.bob_token)
        )
        self.assertEqual(r.status_code, 404)

        # Bob's conversation list does not contain Alice's conversation.
        r = await self.client.get("/api/conversations", headers=self.auth_headers(self.bob_token))
        ids = [c["id"] for c in r.json()]
        self.assertNotIn(conv_id, ids)

    async def test_guest_chat_and_history_rejected(self):
        r = await self.client.post("/api/chat", json={"question": "Guest question"})
        self.assertEqual(r.status_code, 401)
        r = await self.client.get("/api/conversations")
        self.assertEqual(r.status_code, 401)

    async def test_stats_scope_never_crosses_users(self):
        # Alice asks a question (logs a query row under her owner_id).
        await self.chat_sse("What is Alice's salary?", token=self.alice_token)
        # Bob's stats must not count Alice's queries.
        r = await self.client.get("/api/stats", headers=self.auth_headers(self.bob_token))
        body = r.json()
        self.assertEqual(body["scope"], "user")
        self.assertEqual(body["total_queries"], 0)
        for q in body["recent_queries"]:
            self.assertNotIn("Alice", q["question"])
