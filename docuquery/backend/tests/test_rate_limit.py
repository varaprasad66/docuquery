"""Unit tests for rate limiting, login lockout, editions, and cost calculation.

These run against the in-memory fallback (Redis patched out) so they are
deterministic and DB/Redis independent.
"""

import unittest
from unittest.mock import patch

from app.services.costs import calculate_cost_usd
from app.services.rate_limit import LoginLockout, RateLimiter


def _memory_only(cls, *args, **kwargs):
    limiter = cls(*args, **kwargs)
    limiter._get_redis = lambda: None  # force deterministic in-memory backend
    return limiter


class TestRateLimiter(unittest.TestCase):
    def test_allows_under_limit(self):
        rl = _memory_only(RateLimiter, max_requests=3, window_seconds=60)
        for _ in range(3):
            allowed, retry = rl.check("chat:u1")
            self.assertTrue(allowed)
            self.assertEqual(retry, 0)

    def test_blocks_over_limit_with_retry_after(self):
        rl = _memory_only(RateLimiter, max_requests=2, window_seconds=60)
        rl.check("chat:u1")
        rl.check("chat:u1")
        allowed, retry = rl.check("chat:u1")
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry, 1)
        self.assertLessEqual(retry, 60)

    def test_identities_are_isolated(self):
        rl = _memory_only(RateLimiter, max_requests=1, window_seconds=60)
        self.assertTrue(rl.check("chat:a")[0])
        self.assertTrue(rl.check("chat:b")[0])
        self.assertFalse(rl.check("chat:a")[0])

    def test_window_slides(self):
        rl = _memory_only(RateLimiter, max_requests=1, window_seconds=1)
        self.assertTrue(rl.check("chat:a")[0])
        self.assertFalse(rl.check("chat:a")[0])
        import time

        time.sleep(1.1)
        self.assertTrue(rl.check("chat:a")[0])

    def test_memory_is_bounded(self):
        rl = _memory_only(RateLimiter, max_requests=1, window_seconds=1)
        for i in range(10500):
            rl.check(f"chat:ip-{i}")
        self.assertLessEqual(len(rl._memory), 10_000)


class TestLoginLockout(unittest.TestCase):
    def setUp(self):
        self.lock = _memory_only(LoginLockout, 3, 900)

    def test_not_locked_initially(self):
        self.assertEqual(self.lock.is_locked("a|x"), 0)

    def test_locks_after_max_failures(self):
        for _ in range(3):
            self.lock.register_failure("a|x")
        remaining = self.lock.is_locked("a|x")
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 900)

    def test_below_threshold_never_locks(self):
        self.lock.register_failure("a|x")
        self.lock.register_failure("a|x")
        self.assertEqual(self.lock.is_locked("a|x"), 0)

    def test_clear_resets(self):
        for _ in range(3):
            self.lock.register_failure("a|x")
        self.lock.clear("a|x")
        self.assertEqual(self.lock.is_locked("a|x"), 0)

    def test_identities_isolated(self):
        for _ in range(3):
            self.lock.register_failure("victim|x")
        self.assertEqual(self.lock.is_locked("other|x"), 0)


class TestCosts(unittest.TestCase):
    def test_zero_price_models_cost_zero(self):
        with patch("app.services.costs.get_settings") as gs:
            s = gs.return_value
            s.llm_cost_prompt_per_1k = 0.0
            s.llm_cost_completion_per_1k = 0.0
            self.assertEqual(calculate_cost_usd(1000, 2000), 0.0)

    def test_pricing_math(self):
        with patch("app.services.costs.get_settings") as gs:
            s = gs.return_value
            s.llm_cost_prompt_per_1k = 0.5
            s.llm_cost_completion_per_1k = 1.5
            # 2k prompt tokens * 0.5/1k = 1.0 ; 1k completion * 1.5/1k = 1.5
            self.assertAlmostEqual(calculate_cost_usd(2000, 1000), 2.5)


class TestEditions(unittest.TestCase):
    def test_rank_order(self):
        from app.editions import BASIC, ENTERPRISE, PROFESSIONAL, edition_rank

        self.assertLess(edition_rank(BASIC), edition_rank(PROFESSIONAL))
        self.assertLess(edition_rank(PROFESSIONAL), edition_rank(ENTERPRISE))

    def test_has_edition_inclusive(self):
        from app.editions import BASIC, ENTERPRISE, PROFESSIONAL

        with patch("app.editions.current_edition", return_value=PROFESSIONAL):
            from app.editions import has_edition

            self.assertTrue(has_edition(BASIC))
            self.assertTrue(has_edition(PROFESSIONAL))
            self.assertFalse(has_edition(ENTERPRISE))

    def test_unknown_edition_falls_safe_to_enterprise(self):
        from app.editions import edition_rank

        self.assertEqual(edition_rank("garbage"), 3)


class TestRRFFusion(unittest.TestCase):
    def _src(self, cid, name="d.pdf", page=1, content="c", matched_by=None):
        from app.services.retrieval import Source

        return Source(
            chunk_id=cid, document_id=cid, document_name=name, page=page, content=content,
            matched_by=list(matched_by or []),
        )

    def test_fusion_merges_and_reranks(self):
        import uuid

        from app.services.retrieval import fuse_rrf

        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        vector_hits = [self._src(a, matched_by=["vector"]), self._src(b, matched_by=["vector"])]
        keyword_hits = [self._src(b, matched_by=["bm25"]), self._src(c, matched_by=["bm25"])]
        fused = fuse_rrf(vector_hits, keyword_hits, top_k=3)
        # b appears in both legs -> fused score 2/(60+1) beats each single leg.
        self.assertEqual(fused[0].chunk_id, b)
        self.assertEqual(len(fused), 3)
        self.assertEqual(set(fused[0].matched_by), {"vector", "bm25"})

    def test_fusion_respects_top_k(self):
        import uuid

        from app.services.retrieval import fuse_rrf

        ids = [uuid.uuid4() for _ in range(5)]
        hits = [self._src(i) for i in ids]
        fused = fuse_rrf(hits, [], top_k=2)
        self.assertEqual(len(fused), 2)

    def test_fusion_deterministic(self):
        import uuid

        from app.services.retrieval import fuse_rrf

        ids = [uuid.uuid4() for _ in range(4)]
        hits = [self._src(i) for i in ids]
        again = fuse_rrf([self._src(i) for i in ids], [], top_k=4)
        self.assertEqual([s.chunk_id for s in fuse_rrf(hits, [], top_k=4)],
                         [s.chunk_id for s in again])


if __name__ == "__main__":
    unittest.main()
