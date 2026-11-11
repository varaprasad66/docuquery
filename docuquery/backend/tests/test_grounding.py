import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app.config import get_settings

from app.services.grounding import REFUSAL, verify_answer


class TestGrounding(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for key, value in {"grounding_mode": "strict", "grounding_auto_attribution": False, "grounding_fuzzy_quotes": False}.items():
            setting = patch.object(get_settings(), key, value)
            setting.start()
            self.addCleanup(setting.stop)
        self.sources = [SimpleNamespace(content="Annual leave is 25 days per year."),
                        SimpleNamespace(content="Invoices must be paid within 30 days.")]
        self.citations = [{"n": 1}, {"n": 2}]
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())))

    def verdict(self, value):
        self.client.chat.completions.create.return_value = SimpleNamespace(
            usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value)))])

    async def test_keeps_only_cited_supported_source(self):
        self.verdict({"supported": True, "evidence": [{"n": 1, "quote": self.sources[0].content}]})
        answer, citations, *_ = await verify_answer(self.client, "Annual leave?", "25 days [1].", self.sources, self.citations)
        self.assertEqual(answer, "25 days [1].")
        self.assertEqual(citations, [{"n": 1, "snippet": self.sources[0].content}])

    async def test_outside_knowledge_rejected(self):
        self.verdict({"supported": False, "evidence": []})
        answer, citations, *_ = await verify_answer(self.client, "Largest country?", "Russia [1].", self.sources, self.citations)
        self.assertEqual((answer, citations), (REFUSAL, []))

    async def test_supported_partial_answer_survives_missing_second_part(self):
        self.verdict({"supported": True, "evidence": [{"n": 1, "quote": self.sources[0].content}]})
        draft = "Annual leave is 25 days [1]. " + REFUSAL
        answer, citations, *_ = await verify_answer(self.client, "How much leave and who is CEO?", draft, self.sources, self.citations)
        self.assertEqual(answer, draft)
        self.assertEqual([c["n"] for c in citations], [1])

    async def test_fabricated_quote_rejected_even_when_verifier_approves(self):
        self.verdict({"supported": True, "evidence": [{"n": 1, "quote": "Russia is the largest country."}]})
        answer, citations, *_ = await verify_answer(self.client, "Largest country?", "Russia [1].", self.sources, self.citations)
        self.assertEqual((answer, citations), (REFUSAL, []))

    async def test_missing_and_invalid_markers_do_not_call_model(self):
        for draft in ("Russia.", "Russia [99]."):
            answer, citations, *_ = await verify_answer(self.client, "Largest country?", draft, self.sources, self.citations)
            self.assertEqual((answer, citations), (REFUSAL, []))
        self.client.chat.completions.create.assert_not_awaited()

    async def test_no_sources_has_no_citations(self):
        answer, citations, *_ = await verify_answer(self.client, "Question", "Answer [1]", [], [])
        self.assertEqual((answer, citations), (REFUSAL, []))

    async def test_unavailable_verifier_does_not_accept_draft(self):
        self.client.chat.completions.create.side_effect = TimeoutError()
        result = await verify_answer(self.client, "Question", "Answer [1]", self.sources, self.citations)
        self.assertEqual(result.reason, "verifier_error")
        self.assertEqual(result.answer, REFUSAL)
