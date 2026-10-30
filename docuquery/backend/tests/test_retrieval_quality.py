"""Unit tests for Phase 2 retrieval quality: the follow-up question rewriter
and the optional cross-encoder reranker. No database required."""

import asyncio
import types
import unittest
from unittest import mock

from app.config import get_settings
from app.services import query_rewriter, reranker
from app.services.retrieval import Source
from app.services.semantic_cache import scope_hash


def _msg(role: str, content: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(role=role, content=content)


def _fake_llm(reply: str | Exception):
    """Patch query_rewriter.llm_client with a client whose create() returns `reply`."""

    class _Completions:
        async def create(self, **kwargs):
            if isinstance(reply, Exception):
                raise reply
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=reply))]
            )

    class _Client:
        chat = types.SimpleNamespace(completions=_Completions())

    return mock.patch.object(query_rewriter, "llm_client", return_value=_Client())


class TestQueryRewriter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = get_settings()
        self._orig_enabled = self.settings.query_rewrite_enabled
        self._orig_timeout = self.settings.query_rewrite_timeout_seconds

    def tearDown(self):
        self.settings.query_rewrite_enabled = self._orig_enabled
        self.settings.query_rewrite_timeout_seconds = self._orig_timeout

    async def test_no_history_returns_original(self):
        result = await query_rewriter.rewrite_question("What about page 2?", [])
        self.assertEqual(result, "What about page 2?")

    async def test_single_message_history_returns_original(self):
        # Only the just-asked question: nothing to resolve.
        history = [_msg("user", "What about page 2?")]
        result = await query_rewriter.rewrite_question("What about page 2?", history)
        self.assertEqual(result, "What about page 2?")

    async def test_disabled_flag_skips_llm(self):
        self.settings.query_rewrite_enabled = False
        history = [_msg("user", "Tell me about the handbook"), _msg("assistant", "It covers leave.")]
        with _fake_llm("SHOULD NOT BE CALLED") as fake_client:
            result = await query_rewriter.rewrite_question("and page 2?", history)
        self.assertEqual(result, "and page 2?")
        fake_client.assert_not_called()

    async def test_rewrites_followup_with_history(self):
        history = [
            _msg("user", "Summarize the employee handbook"),
            _msg("assistant", "The handbook covers annual leave and remote work."),
        ]
        with _fake_llm("Summarize the employee handbook's remote work policy"):
            result = await query_rewriter.rewrite_question("what about remote work?", history)
        self.assertEqual(result, "Summarize the employee handbook's remote work policy")

    async def test_llm_failure_falls_back_to_original(self):
        history = [_msg("user", "q1"), _msg("assistant", "a1")]
        with _fake_llm(RuntimeError("LLM down")):
            result = await query_rewriter.rewrite_question("follow up?", history)
        self.assertEqual(result, "follow up?")

    async def test_timeout_falls_back_to_original(self):
        self.settings.query_rewrite_timeout_seconds = 0  # immediate timeout

        class _Completions:
            async def create(self, **kwargs):
                await asyncio.sleep(5)
                raise AssertionError("should be cancelled")

        class _Client:
            chat = types.SimpleNamespace(completions=_Completions())

        history = [_msg("user", "q1"), _msg("assistant", "a1")]
        with mock.patch.object(query_rewriter, "llm_client", return_value=_Client()):
            result = await query_rewriter.rewrite_question("follow up?", history)
        self.assertEqual(result, "follow up?")

    async def test_empty_or_oversized_reply_falls_back(self):
        history = [_msg("user", "q1"), _msg("assistant", "a1")]
        for bad in ("", "x" * 1500, "line1\n\nline2"):
            with _fake_llm(bad):
                result = await query_rewriter.rewrite_question("follow up?", history)
            self.assertEqual(result, "follow up?", bad)


class TestReranker(unittest.TestCase):
    def setUp(self):
        reranker.reset_model()

    def tearDown(self):
        reranker.reset_model()

    @staticmethod
    def _sources(*contents: str) -> list[Source]:
        return [
            Source(
                chunk_id=__import__("uuid").uuid4(),
                document_id=__import__("uuid").uuid4(),
                document_name="d.pdf",
                page=i,
                content=c,
            )
            for i, c in enumerate(contents, start=1)
        ]

    def test_reranks_by_cross_encoder_score(self):
        sources = self._sources("weak match", "strongest match here", "medium match")

        class _FakeModel:
            def predict(self, pairs):
                # Score by content length as a deterministic stand-in.
                return [float(len(text)) for _, text in pairs]

        with mock.patch.object(reranker, "_load_model", return_value=_FakeModel()):
            result = reranker.rerank_sources("match", sources, top_k=2)

        self.assertEqual([s.content for s in result], ["strongest match here", "medium match"])

    def test_failure_falls_back_to_rrf_order(self):
        sources = self._sources("first", "second", "third")
        with mock.patch.object(reranker, "_load_model", side_effect=RuntimeError("no model")):
            result = reranker.rerank_sources("q", sources, top_k=2)
        self.assertEqual([s.content for s in result], ["first", "second"])

    def test_empty_sources_pass_through(self):
        self.assertEqual(reranker.rerank_sources("q", [], top_k=3), [])


class TestCacheScopeHash(unittest.TestCase):
    def test_document_filter_changes_scope(self):
        base = scope_hash("11111111-1111-1111-1111-111111111111")
        filtered = scope_hash(
            "11111111-1111-1111-1111-111111111111",
            ["22222222-2222-2222-2222-222222222222"],
        )
        self.assertNotEqual(base, filtered)
        # The same filter always produces the same scope.
        self.assertEqual(
            filtered,
            scope_hash(
                "11111111-1111-1111-1111-111111111111",
                ["22222222-2222-2222-2222-222222222222"],
            ),
        )
        # Filter order must not matter.
        self.assertEqual(
            scope_hash(None, ["a", "b"]),
            scope_hash(None, ["b", "a"]),
        )


if __name__ == "__main__":
    unittest.main()
