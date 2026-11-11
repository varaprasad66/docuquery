import json
import unittest
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, patch

from app.config import get_settings
from app.services.grounding import REFUSAL, verify_answer
from app.services.prompting import build_prompt


class TestPhase1(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = get_settings()
        self.config("grounding_mode", "strict")
        self.config("grounding_auto_attribution", False)
        # context_text mirrors content: no neighbor expansion in these fixtures.
        self.sources = [Obj(content="Annual leave is 25 days per year.", context_text="Annual leave is 25 days per year."),
                        Obj(content="Invoices are due within thirty days.", context_text="Invoices are due within thirty days.")]
        self.citations = [{"n": 1}, {"n": 2}]
        self.create = AsyncMock()
        self.client = Obj(chat=Obj(completions=Obj(create=self.create)))

    def config(self, name, value):
        p = patch.object(self.settings, name, value)
        p.start()
        self.addCleanup(p.stop)

    def verdict(self, supported=True, quote=None):
        self.create.return_value = Obj(usage=None, choices=[Obj(message=Obj(content=json.dumps({
            "supported": supported, "evidence": [{"n": 1, "quote": quote or self.sources[0].content}]
        })))])

    async def check(self, draft="25 days [1].", sources=None):
        return await verify_answer(self.client, "Leave?", draft, self.sources if sources is None else sources, self.citations)

    async def test_all_refusal_reasons(self):
        self.assertEqual((await self.check(sources=[])).reason, "no_sources")
        self.assertEqual((await self.check("")).reason, "empty_draft")
        self.assertEqual((await self.check(REFUSAL)).reason, "model_refused")
        self.assertEqual((await self.check("25 days")).reason, "no_citation_markers")
        self.assertEqual((await self.check("25 days [99]")).reason, "invalid_marker")
        self.create.return_value = Obj(usage=None, choices=[Obj(message=Obj(content="not json"))])
        self.assertEqual((await self.check()).reason, "verifier_bad_json")
        self.verdict(False)
        self.assertEqual((await self.check()).reason, "verifier_rejected")
        self.verdict(quote="Bananas grow on enormous trees.")
        self.assertEqual((await self.check()).reason, "quote_mismatch")
        self.verdict()
        self.assertEqual((await self.check()).reason, "ok")

    async def test_soft_keeps_good_sentence(self):
        self.config("grounding_mode", "soft")
        self.verdict()
        result = await self.check("Leave is 25 days [1]. Russia is largest [2].")
        self.assertEqual(result.answer, "Leave is 25 days [1].")
        self.assertEqual([c["n"] for c in result.citations], [1])

    async def test_auto_attribution_and_timeout_fallback(self):
        self.config("grounding_mode", "soft")
        self.config("grounding_auto_attribution", True)
        # attribute() embeds answer sentences (task="query") and sources
        # (task="document") in two separate calls; each call returns a list of
        # vectors — one per input text.
        embed = AsyncMock(side_effect=[[[1, 0]], [[1, 0], [0, 1]]])
        with patch("app.services.grounding.embed_texts", embed):
            result = await self.check("Leave is 25 days.")
            self.assertIn("[1]", result.answer)
            self.assertTrue(result.unverified)
            self.assertEqual(embed.await_args_list[0].kwargs.get("task"), "query")
            self.assertEqual(embed.await_args_list[1].kwargs.get("task"), "document")
            self.create.side_effect = TimeoutError()
            embed.side_effect = [[[1, 0]], [[1, 0], [0, 1]]]  # fresh vectors for the fallback path
            result = await self.check("Leave is 25 days [1].")
            self.assertTrue(result.unverified)
            self.assertEqual(result.reason, "verifier_error")

    async def test_fuzzy_normalized_quote(self):
        self.verdict(quote="Annual leave is 25 days per year!")
        self.assertEqual((await self.check()).reason, "ok")

    async def test_separate_verifier_model(self):
        self.config("verifier_model", "verifier-test")
        self.verdict()
        await self.check()
        self.assertEqual(self.create.call_args.kwargs["model"], "verifier-test")

    def test_prompt_budget_removes_low_ranked_sources(self):
        self.config("llm_context_budget_tokens", 600)
        self.config("prompt_budget_enabled", True)
        sources = [Obj(content="x"*1000) for _ in range(6)]
        selected, _, messages, chars = build_prompt("Original", "Standalone", [], sources, lambda values: "\n".join(v.content for v in values))
        self.assertLess(len(selected), 6)
        self.assertLessEqual((chars+3)//4 + 8*len(messages), 600)
        self.assertIn("Standalone", messages[-1]["content"])
