"""Unit tests for retrieval data models and rank fusion logic."""

import unittest
import uuid

from app.services.retrieval import Source, _vector_literal


class TestRetrieval(unittest.TestCase):
    def test_vector_literal_formatting(self):
        vec = [0.12345678, -0.98765432, 1.0]
        literal = _vector_literal(vec)
        self.assertTrue(literal.startswith("["))
        self.assertTrue(literal.endswith("]"))
        self.assertIn("0.1234568", literal)
        self.assertIn("-0.9876543", literal)

    def test_source_snippet_truncation(self):
        short_source = Source(
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            document_name="doc.pdf",
            page=1,
            content="Short content here.",
        )
        self.assertEqual(short_source.snippet, "Short content here.")

        long_text = "Word " * 100
        long_source = Source(
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            document_name="doc.pdf",
            page=1,
            content=long_text,
        )
        self.assertLessEqual(len(long_source.snippet), 283)
        self.assertTrue(long_source.snippet.endswith("..."))


if __name__ == "__main__":
    unittest.main()
