"""Chat pipeline: SSE streaming, citations, semantic cache (cost optimization),
quotas, feedback loop, and sanitized errors."""

import types
import uuid

from tests_integration import IntegrationTestCase


class TestChatPipeline(IntegrationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.email = f"chat-{uuid.uuid4().hex[:8]}@test.io"
        self.user = await self.register(self.email)
        self.token = self.user["access_token"]
        doc = await self.upload_pdf(
            self.token,
            filename="handbook.pdf",
            text="Annual leave is 25 days per year and rolls over up to 10 days.",
        )
        await self.process_document(doc["id"])
        self.doc_id = doc["id"]

    async def test_chat_streams_cited_answer(self):
        events = await self.chat_sse("How many annual leave days do I get?", token=self.token)
        types = [e["type"] for e in events]

        self.assertIn("meta", types)
        self.assertIn("citations", types)
        self.assertIn("token", types)
        self.assertIn("done", types)

        citations = next(e for e in events if e["type"] == "citations")
        self.assertEqual(citations["citations"][0]["document_name"], "handbook.pdf")
        self.assertEqual(citations["citations"][0]["page"], 1)

        answer = "".join(e["value"] for e in events if e["type"] == "token")
        self.assertIn("25 days", answer)

        done = next(e for e in events if e["type"] == "done")
        self.assertGreater(done["prompt_tokens"], 0)
        self.assertGreater(done["completion_tokens"], 0)

    async def test_conversation_history_persists(self):
        events = await self.chat_sse("First question", token=self.token)
        conv_id = next(e for e in events if e["type"] == "meta")["conversation_id"]

        r = await self.client.get(
            f"/api/conversations/{conv_id}", headers=self.auth_headers(self.token)
        )
        self.assertEqual(r.status_code, 200)
        messages = r.json()
        self.assertEqual(len(messages), 2)  # question + streamed answer
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[1]["role"], "assistant")

    async def test_semantic_cache_serves_repeated_question(self):
        e1 = await self.chat_sse("How many annual leave days do I get?", token=self.token)
        self.assertFalse(next(e for e in e1 if e["type"] == "meta")["cached"])

        e2 = await self.chat_sse("How many annual leave days do I get?", token=self.token)
        meta2 = next(e for e in e2 if e["type"] == "meta")
        done2 = next(e for e in e2 if e["type"] == "done")
        self.assertTrue(meta2["cached"], "Identical question must be served from cache")
        self.assertTrue(done2["cached"])
        self.assertEqual(done2["prompt_tokens"], 0, "Cache hits cost zero prompt tokens")
        answer = "".join(e["value"] for e in e2 if e["type"] == "token")
        self.assertIn("25 days", answer)

    async def test_cache_is_scoped_per_user(self):
        # Alice asks and populates the cache under her scope.
        await self.chat_sse("How many annual leave days do I get?", token=self.token)
        # A different user with the same question must NOT hit Alice's cache entry.
        other = await self.register(f"other-{uuid.uuid4().hex[:8]}@test.io")
        e = await self.chat_sse(
            "How many annual leave days do I get?", token=other["access_token"]
        )
        self.assertFalse(next(e for e in e if e["type"] == "meta")["cached"])

    async def test_cache_invalidated_when_document_deleted(self):
        await self.chat_sse("How many annual leave days do I get?", token=self.token)
        await self.client.delete(
            f"/api/documents/{self.doc_id}", headers=self.auth_headers(self.token)
        )
        # Re-upload same content -> ask again -> must NOT be served from stale cache.
        doc2 = await self.upload_pdf(
            self.token,
            filename="handbook2.pdf",
            text="Annual leave is 25 days per year and rolls over up to 10 days.",
        )
        await self.process_document(doc2["id"])
        e = await self.chat_sse("How many annual leave days do I get?", token=self.token)
        self.assertFalse(next(e for e in e if e["type"] == "meta")["cached"])

    async def test_daily_question_quota_enforced(self):
        from app.config import get_settings

        settings = get_settings()
        original = settings.max_daily_questions
        settings.max_daily_questions = 1
        try:
            events = await self.chat_sse("Question one", token=self.token)
            self.assertIn("done", [e["type"] for e in events])

            r = await self.client.post(
                "/api/chat", json={"question": "Question two"},
                headers=self.auth_headers(self.token),
            )
            self.assertEqual(r.status_code, 429, "Second question must be quota-blocked")
        finally:
            settings.max_daily_questions = original

    async def test_feedback_roundtrip(self):
        events = await self.chat_sse("Rate me", token=self.token)
        done = next(e for e in events if e["type"] == "done")
        message_id = done["message_id"]

        r = await self.client.post(
            f"/api/messages/{message_id}/feedback",
            json={"rating": "up", "comment": "Helpful"},
            headers=self.auth_headers(self.token),
        )
        self.assertEqual(r.status_code, 204)

        conv_id = next(e for e in events if e["type"] == "meta")["conversation_id"]
        r = await self.client.get(
            f"/api/conversations/{conv_id}", headers=self.auth_headers(self.token)
        )
        assistant = [m for m in r.json() if m["role"] == "assistant"]
        self.assertEqual(assistant[0]["feedback_rating"], "up")

    async def test_feedback_requires_conversation_owner(self):
        events = await self.chat_sse("Rate me too", token=self.token)
        done = next(e for e in events if e["type"] == "done")
        other = await self.register(f"fb-{uuid.uuid4().hex[:8]}@test.io")
        r = await self.client.post(
            f"/api/messages/{done['message_id']}/feedback",
            json={"rating": "down"},
            headers=self.auth_headers(other["access_token"]),
        )
        self.assertEqual(r.status_code, 404)

    async def test_llm_failure_streams_sanitized_error(self):
        from unittest.mock import patch

        async def broken_create(**kwargs):
            raise RuntimeError("SECRET-INTERNAL-DETAILS connection refused")

        class BrokenClient:
            class chat:  # noqa: N801
                class completions:  # noqa: N801
                    create = staticmethod(broken_create)

        with patch("app.api.chat.llm_client", return_value=BrokenClient()):
            r = await self.client.post(
                "/api/chat", json={"question": "anything"},
                headers=self.auth_headers(self.token),
            )
        self.assertNotIn("SECRET-INTERNAL-DETAILS", r.text, "Internal error details must never reach the client")
        self.assertIn("temporarily unavailable", r.text)


class TestCacheDocumentFilterScope(IntegrationTestCase):
    """The semantic cache key must include the document filter: the same
    question asked with different document_ids must never share a cache entry."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.email = f"scope-{uuid.uuid4().hex[:8]}@test.io"
        self.user = await self.register(self.email)
        self.token = self.user["access_token"]
        self.doc_a = await self.upload_pdf(
            self.token, filename="alpha.pdf", text="Alpha document unique marker zqalpha with leave policy."
        )
        self.doc_b = await self.upload_pdf(
            self.token, filename="beta.pdf", text="Beta document unique marker zqbeta with expense policy."
        )
        await self.process_document(self.doc_a["id"])
        await self.process_document(self.doc_b["id"])

    async def _chat(self, **kwargs) -> list[dict]:
        return await self.chat_sse("What policies do these documents contain?", token=self.token, **kwargs)

    async def test_same_filter_repeats_hit_cache(self):
        filter_a = {"document_ids": [self.doc_a["id"]]}
        e1 = await self._chat(**filter_a)
        e2 = await self._chat(**filter_a)
        self.assertFalse(next(e for e in e1 if e["type"] == "meta")["cached"])
        self.assertTrue(next(e for e in e2 if e["type"] == "meta")["cached"])

    async def test_different_filter_never_hits_cache(self):
        e1 = await self._chat(document_ids=[self.doc_a["id"]])
        self.assertFalse(next(e for e in e1 if e["type"] == "meta")["cached"])

        e2 = await self._chat(document_ids=[self.doc_b["id"]])
        self.assertFalse(next(e for e in e2 if e["type"] == "meta")["cached"])

    async def test_unfiltered_never_hits_filtered_cache(self):
        e1 = await self._chat(document_ids=[self.doc_a["id"]])
        e2 = await self._chat()
        self.assertFalse(next(e for e in e1 if e["type"] == "meta")["cached"])
        self.assertFalse(next(e for e in e2 if e["type"] == "meta")["cached"])


class TestFollowUpRewriter(IntegrationTestCase):
    """With history, the follow-up is rewritten into a standalone query before
    retrieval (LLM patched); with no history the original question is used."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from unittest.mock import patch

        import app.services.query_rewriter as qr

        self.email = f"rw-{uuid.uuid4().hex[:8]}@test.io"
        self.user = await self.register(self.email)
        self.token = self.user["access_token"]
        self.doc = await self.upload_pdf(
            self.token, filename="handbook.pdf",
            text="Remote work policy: employees may work remotely three days per week marker zqremote.",
        )
        await self.process_document(self.doc["id"])

        class _Completions:
            async def create(self, **kwargs):
                prompt = kwargs["messages"][-1]["content"]
                assert "LATEST QUESTION" in prompt, prompt
                return types.SimpleNamespace(
                    choices=[
                        types.SimpleNamespace(
                            message=types.SimpleNamespace(
                                content="handbook remote work policy zqremote"
                            )
                        )
                    ]
                )

        class _Client:
            chat = types.SimpleNamespace(completions=_Completions())

        self._patcher = patch.object(qr, "llm_client", return_value=_Client())
        self._patcher.start()
        self.addAsyncCleanup(self._patcher.stop)

    async def test_followup_reaches_retrieval_as_standalone_query(self):
        e1 = await self.chat_sse("Summarize the employee handbook", token=self.token)
        conv_id = next(e for e in e1 if e["type"] == "meta")["conversation_id"]

        e2 = await self.chat_sse("what about remote work?", token=self.token, conversation_id=conv_id)
        citations = next(e for e in e2 if e["type"] == "citations")["citations"]
        self.assertTrue(citations)
        self.assertEqual(citations[0]["document_name"], "handbook.pdf")

    async def test_first_question_skips_rewriter(self):
        from app.services import query_rewriter as qr

        self.assertFalse(qr.needs_rewrite([]))
        events = await self.chat_sse("How many annual leave days do I get?", token=self.token)
        self.assertIn("citations", [e["type"] for e in events])
