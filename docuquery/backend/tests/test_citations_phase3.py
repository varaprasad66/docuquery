"""Phase 3 citations: quote location offsets and first-appearance renumbering."""

import unittest

from app.config import get_settings
from app.services.grounding import (
    _normalized_index_map,
    locate_quote,
    normalize_quote,
    renumber_citations,
)


class TestRenumberCitations(unittest.TestCase):
    def test_order_of_first_appearance(self):
        answer = "Payment is due in 30 days [2]. Leave is 25 days [1]. Both [1][2]."
        citations = [{"n": 1, "document_name": "a.pdf"}, {"n": 2, "document_name": "b.pdf"}]
        text, out = renumber_citations(answer, citations)
        # One consistent global mapping: old [2] -> new [1], old [1] -> new [2],
        # everywhere — the first sentence fixes the numbering for the whole text.
        self.assertEqual(text, "Payment is due in 30 days [1]. Leave is 25 days [2]. Both [2][1].")
        self.assertEqual([c["n"] for c in out], [1, 2])
        self.assertEqual(out[0]["document_name"], "b.pdf")
        self.assertEqual(out[1]["document_name"], "a.pdf")

    def test_unreferenced_citations_dropped(self):
        text, out = renumber_citations("Only one [2].", [{"n": 1}, {"n": 2}])
        self.assertEqual(text, "Only one [1].")
        self.assertEqual([c["n"] for c in out], [1])

    def test_gaps_closed_after_soft_drop(self):
        text, out = renumber_citations("Kept [3].", [{"n": 1}, {"n": 2}, {"n": 3}])
        self.assertEqual(text, "Kept [1].")
        self.assertEqual(out[0]["n"], 1)

    def test_no_markers_returns_original(self):
        text, out = renumber_citations("No markers here.", [{"n": 1}])
        self.assertEqual(text, "No markers here.")
        self.assertEqual(out, [])

    def test_dangling_marker_left_untouched(self):
        text, out = renumber_citations("Claims [9] outside the source list.", [{"n": 1}])
        self.assertEqual(text, "Claims [9] outside the source list.")
        self.assertEqual(out, [])


class TestLocateQuote(unittest.TestCase):
    CONTENT = "Annual  leave is 25 days\nper  year with rollover up to 10 days."

    def test_exact_substring_offsets(self):
        start, end = locate_quote(self.CONTENT, "25 days")
        self.assertEqual(self.CONTENT[start:end], "25 days")

    def test_fuzzy_whitespace_normalization(self):
        quote = "annual leave is 25 days per year"  # single spaces, no newline
        offsets = locate_quote(self.CONTENT, quote)
        self.assertIsNotNone(offsets)
        start, end = offsets
        self.assertEqual(" ".join(self.CONTENT[start:end].split()).casefold(), quote)

    def test_hyphenated_linebreak_matched(self):
        content = "The employ-\nment contract allows 25 days of paid leave."
        quote = "The employment contract allows 25 days"
        start, end = locate_quote(content, quote)
        # The raw slice still carries the line-break hyphen; normalized, it is
        # exactly the quote (this is what the UI highlight represents).
        self.assertEqual(normalize_quote(content[start:end]), normalize_quote(quote))

    def test_miss_returns_none(self):
        self.assertIsNone(
            locate_quote("Quarterly revenue exceeded projections.", "Bananas grow on enormous trees")
        )

    def test_weak_fuzzy_alignment_rejected(self):
        # No real overlap: the best partial window must not become an offset.
        self.assertIsNone(locate_quote("Annual leave is 25 days per year.", "zzz qqq www xxx"))

    def test_short_quote_skips_fuzzy(self):
        # Not a substring, too short to fuzzy-align -> no confident offsets.
        self.assertIsNone(locate_quote("The contract", "abc"))

    def test_unicode_and_soft_hyphen(self):
        content = "Café coop\u00aderation is vital, with 25 days leave."
        quote = "Café cooperation is vital"
        start, end = locate_quote(content, quote)
        self.assertEqual(normalize_quote(content[start:end]), normalize_quote(quote))

    def test_index_map_covers_all_normalized_chars(self):
        source = "a  b\t\nc"
        normalized, positions = _normalized_index_map(source)
        self.assertEqual(normalized, "a b c")
        self.assertEqual(len(normalized), len(positions))
        self.assertTrue(all(0 <= p < len(source) for p in positions))


class TestThresholdConfig(unittest.TestCase):
    def test_quote_threshold_is_configurable(self):
        # Sanity: the score gate reads the same setting verification uses.
        self.assertGreaterEqual(get_settings().grounding_quote_threshold, 0)
        self.assertLessEqual(get_settings().grounding_quote_threshold, 100)


if __name__ == "__main__":
    unittest.main()
