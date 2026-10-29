"""Unit tests for PDF extraction and page parsing."""

from pathlib import Path
import unittest

from app.services.pdf import _clean_text, extract_pages, page_count

SAMPLE_PDF_24 = (
    Path(__file__).parent.parent / "sample_data" / "On_the_Creativity_of_AI_Agents-1.pdf"
)


class TestPDFService(unittest.TestCase):
    def test_clean_text(self):
        dirty = "hello\x00world\x00\n  test  "
        cleaned = _clean_text(dirty)
        self.assertEqual(cleaned, "helloworld\n  test")

    def test_page_count_24(self):
        if not SAMPLE_PDF_24.exists():
            self.skipTest(f"Sample PDF {SAMPLE_PDF_24} not found")
        count = page_count(SAMPLE_PDF_24)
        self.assertEqual(count, 24)

    def test_extract_pages_24_with_progress(self):
        if not SAMPLE_PDF_24.exists():
            self.skipTest(f"Sample PDF {SAMPLE_PDF_24} not found")

        progress_calls = []

        def cb(curr, total):
            progress_calls.append((curr, total))

        pages = extract_pages(SAMPLE_PDF_24, progress_cb=cb)
        self.assertEqual(len(pages), 24)
        self.assertEqual(len(progress_calls), 24)
        self.assertEqual(progress_calls[-1], (24, 24))

        # Check page text integrity
        for p in pages:
            self.assertGreater(len(p.text), 100)
            self.assertNotIn("\x00", p.text)
            self.assertIn(p.method, ("text", "ocr"))


if __name__ == "__main__":
    unittest.main()
