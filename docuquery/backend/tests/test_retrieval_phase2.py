"""Phase 2 retrieval quality: embedding task prefixes, keyword tsquery builder,
neighbor expansion, reranker guarantees, relevance floor decision."""

import math
import unittest
import uuid
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, patch

from app.config import get_settings
from app.services.embeddings import contextual_embed_input, embedding_version, embed_texts
from app.services.retrieval import (
    Source,
    build_keyword_query,
    fuse_rrf,
    merge_neighbors,
)
from app.services.reranker import rerank_sources


def _source(page=1, content="Annual leave is 25 days.", matched_by=("vector",), **kwargs):
    return Source(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_name="handbook.pdf",
        page=page,
        content=content,
        matched_by=list(matched_by),
        **kwargs,
    )


class TestEmbedTaskPrefixes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = get_settings()
        for name, value in (
            ("embed_doc_prefix", "search_document: "),
            ("embed_query_prefix", "search_query: "),
            ("embeddings_model", "nomic-embed-text"),
            ("embedding_version_tag", "prefix-v1"),
            ("embed_dim", 4),
        ):
            p = patch.object(self.settings, name, value)
            p.start()
            self.addCleanup(p.stop)

    def _fake_client(self, captured):
        async def create(**kwargs):
            captured.append(kwargs["input"])
            return Obj(data=[Obj(index=i, embedding=[0.1, 0.2, 0.3, 0.4]) for i in range(len(kwargs["input"]))])

        return Obj(embeddings=Obj(create=AsyncMock(side_effect=create)))

    async def test_query_and_document_prefixes_applied(self):
        captured: list = []
        with patch("app.services.embeddings.embeddings_client", return_value=self._fake_client(captured)):
            await embed_texts(["How many days of leave?"], task="query")
            await embed_texts(["Annual leave is 25 days."], task="document")
            await embed_texts(["unprefixed text"])
        self.assertEqual(captured[0], ["search_query: How many days of leave?"])
        self.assertEqual(captured[1], ["search_document: Annual leave is 25 days."])
        self.assertEqual(captured[2], ["unprefixed text"])

    async def test_empty_prefixes_send_raw_text(self):
        captured: list = []
        patch.object(self.settings, "embed_doc_prefix", "").start()
        patch.object(self.settings, "embed_query_prefix", "").start()
        with patch("app.services.embeddings.embeddings_client", return_value=self._fake_client(captured)):
            await embed_texts(["plain"], task="document")
        self.assertEqual(captured[0], ["plain"])

    def test_embedding_version_tracks_prefix_config(self):
        self.assertEqual(embedding_version(), "nomic-embed-text+prefix-v1")
        patch.object(self.settings, "embed_doc_prefix", "").start()
        p = patch.object(self.settings, "embed_query_prefix", "")
        p.start()
        self.addCleanup(p.stop)
        self.assertEqual(embedding_version(), "nomic-embed-text")

    def test_contextual_embed_input_header_and_clean_fallback(self):
        with patch.object(self.settings, "contextual_embeddings", True):
            text = contextual_embed_input("handbook.pdf", 3, "Page 3", "Leave policy text.")
            self.assertTrue(text.startswith("Document: handbook.pdf | Page 3\n"))
            self.assertIn("Leave policy text.", text)
            fallback = contextual_embed_input("handbook.pdf", 3, None, "Body.")
            self.assertTrue(fallback.startswith("Document: handbook.pdf | Page 3\n"))
        with patch.object(self.settings, "contextual_embeddings", False):
            self.assertEqual(contextual_embed_input("handbook.pdf", 3, "Page 3", "Body."), "Body.")


class TestKeywordQuery(unittest.TestCase):
    def test_stop_words_dropped_or_joined(self):
        q = build_keyword_query("What is the annual leave policy?")
        self.assertEqual(q, "annual or leave or policy")

    def test_tsquery_joiner_when_websearch_disabled(self):
        q = build_keyword_query("annual leave", websearch=False)
        self.assertEqual(q, "annual | leave")

    def test_all_stop_words_falls_back_to_terms(self):
        # A query made entirely of stop words still yields every term.
        self.assertEqual(build_keyword_query("What is it?"), "what or is or it")

    def test_syntax_characters_stripped(self):
        q = build_keyword_query("C++ & (Postgres) help!")
        for token in q.split(" or "):
            self.assertRegex(token, r"^[a-z0-9]+$")


class TestNeighborMerge(unittest.TestCase):
    def test_dedupes_shared_neighbor_across_hits(self):
        hit_a = _source(chunk_index=1, content="Hit A.")
        hit_b = _source(chunk_index=2, content="Hit B.")
        shared_prev = _source(content="Shared previous chunk.", chunk_index=0)
        added = merge_neighbors(
            [hit_a, hit_b], {hit_a.chunk_id: [shared_prev], hit_b.chunk_id: [shared_prev]},
            max_added_chars=1000,
        )
        # The shared neighbor attaches once (to the first hit); the second hit,
        # whose id is already in `seen`, receives nothing extra.
        self.assertEqual(added, len(shared_prev.content))
        self.assertEqual(hit_a.neighbor_texts, [shared_prev.content])
        self.assertEqual(hit_b.neighbor_texts, [])

    def test_cap_by_character_budget(self):
        hit = _source(chunk_index=1)
        big = _source(content="x" * 500, chunk_index=0)
        small = _source(content="y" * 10, chunk_index=2)
        merge_neighbors([hit], {hit.chunk_id: [big, small]}, max_added_chars=400)
        self.assertEqual(hit.neighbor_texts, [small.content])

    def test_hit_content_untouched_and_context_text_joined(self):
        hit = _source(chunk_index=1, content="Hit body.")
        prev_chunk = _source(content="Neighbor body.", chunk_index=0)
        merge_neighbors([hit], {hit.chunk_id: [prev_chunk]}, max_added_chars=1000)
        self.assertEqual(hit.content, "Hit body.")
        self.assertEqual(hit.context_text, "Hit body.\n\nNeighbor body.")
        self.assertEqual(hit.snippet, "Hit body.")

    def test_no_neighbors_keeps_content(self):
        self.assertEqual(_source().context_text, "Annual leave is 25 days.")


class TestFuseRrfRelevance(unittest.TestCase):
    def test_relevance_survives_fusion(self):
        vector_hit = _source(matched_by=("vector",), relevance=0.83)
        keyword_hit = _source(matched_by=("bm25",), relevance=0.42)
        vector_hit.chunk_id = keyword_hit.chunk_id = uuid.uuid4()
        fused = fuse_rrf([vector_hit], [keyword_hit], top_k=5)
        self.assertEqual(fused[0].relevance, 0.83)
        self.assertEqual(sorted(fused[0].matched_by), ["bm25", "vector"])


class TestRerankerKeywordGuarantee(unittest.TestCase):
    def setUp(self):
        p = patch.object(get_settings(), "reranker_enabled", True)
        p.start()
        self.addCleanup(p.stop)

    def test_keyword_only_hit_survives_low_cross_encoder_score(self):
        hits = [_source(content=f"vector hit {i}", matched_by=("vector",)) for i in range(5)]
        keyword_hit = _source(content="exact keyword match", matched_by=("bm25",))
        hits.append(keyword_hit)

        class _FakeModel:
            def predict(self, pairs):
                # Keyword match scores worst; every vector hit outranks it.
                return [1.0] * 5 + [-10.0]

        with patch("app.services.reranker._load_model", return_value=_FakeModel()):
            kept = rerank_sources("query", hits, top_k=5)
        self.assertIn(keyword_hit.chunk_id, {s.chunk_id for s in kept})
        self.assertEqual(len(kept), 5)
        # Sigmoid-normalized scores land in (0, 1).
        self.assertTrue(all(0.0 < s.score < 1.0 for s in kept))
        self.assertTrue(all(0.0 < s.relevance < 1.0 for s in kept))

    def test_no_keyword_hits_keeps_pure_order(self):
        hits = [_source(content=f"hit {i}", matched_by=("vector",)) for i in range(4)]

        class _FakeModel:
            def predict(self, pairs):
                return [3.0, 2.0, 1.0, 0.0]

        with patch("app.services.reranker._load_model", return_value=_FakeModel()):
            kept = rerank_sources("query", hits, top_k=2)
        self.assertEqual([s.content for s in kept], ["hit 0", "hit 1"])
        self.assertAlmostEqual(kept[0].score, 1 / (1 + math.exp(-3.0)), places=6)

    def test_failure_falls_back_to_fused_order(self):
        hits = [_source(content=f"hit {i}") for i in range(4)]
        with patch("app.services.reranker._load_model", side_effect=RuntimeError("no model")):
            kept = rerank_sources("query", hits, top_k=2)
        self.assertEqual(len(kept), 2)


if __name__ == "__main__":
    unittest.main()
