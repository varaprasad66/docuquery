"""Unit tests for text chunking and token estimation."""

import unittest

from app.services.chunker import chunk_text, estimate_tokens, hard_split


class TestChunker(unittest.TestCase):
    def test_chunk_text_empty(self):
        self.assertEqual(chunk_text(""), [])
        self.assertEqual(chunk_text("   \n\n  "), [])

    def test_chunk_text_single_short(self):
        text = "This is a short paragraph of text."
        chunks = chunk_text(text, chunk_size=1000, overlap=150)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], text)

    def test_chunk_text_merging(self):
        p1 = "First paragraph with some words."
        p2 = "Second paragraph with more words."
        combined = f"{p1}\n\n{p2}"
        chunks = chunk_text(combined, chunk_size=1000, overlap=150)
        self.assertEqual(len(chunks), 1)
        self.assertIn(p1, chunks[0])
        self.assertIn(p2, chunks[0])

    def test_chunk_text_overlap(self):
        # Create paragraphs that will exceed chunk_size=100
        paragraphs = [f"Paragraph {i}: " + ("x" * 60) for i in range(5)]
        full_text = "\n\n".join(paragraphs)
        chunks = chunk_text(full_text, chunk_size=100, overlap=25)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 150)  # within margin of chunk + overlap

    def test_hard_split_pathological(self):
        # Test long unbroken string (e.g. base64 or URL)
        unbroken = "a" * 500
        pieces = hard_split(unbroken, chunk_size=100, overlap=20)
        self.assertGreater(len(pieces), 1)
        reconstructed = "".join(pieces)
        self.assertIn("aaaaa", reconstructed)

    def test_estimate_tokens(self):
        text = "Hello world"  # 11 chars -> round(11/4) = 3
        self.assertEqual(estimate_tokens(text), 3)
        self.assertEqual(estimate_tokens(""), 1)  # minimum 1


if __name__ == "__main__":
    unittest.main()
