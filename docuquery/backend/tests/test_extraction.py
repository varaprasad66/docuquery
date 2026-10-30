"""Unit tests for the structured extraction core: schema resolution, JSON
parsing (fence stripping), validation with one retry, and citation mapping."""

import json
import unittest

from app.services.extraction import (
    Excerpt,
    ExtractionError,
    TEMPLATES,
    build_messages,
    resolve_schema,
    run_extraction_core,
    _strip_fences,
)
import jsonschema


def _excerpts(*texts: str) -> list[Excerpt]:
    return [
        Excerpt(index=i, document_name="invoice.pdf", location="Page 1", content=t)
        for i, t in enumerate(texts, start=1)
    ]


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "invoice_number": {"type": ["string", "null"]},
            "total_amount": {"type": ["number", "null"]},
        },
    }


def _payload(data: dict, confidence: str = "high", sources: dict | None = None) -> str:
    return json.dumps({"data": data, "confidence": confidence, "field_sources": sources or {}})


class TestSchemaResolution(unittest.TestCase):
    def test_builtin_template(self):
        name, schema = resolve_schema("invoice", None)
        self.assertEqual(name, "invoice")
        self.assertEqual(schema, TEMPLATES["invoice"]["schema"])

    def test_custom_schema(self):
        name, schema = resolve_schema(None, _schema())
        self.assertEqual(name, "custom")
        self.assertEqual(schema, _schema())

    def test_both_or_neither_rejected(self):
        with self.assertRaisesRegex(ValueError, "both"):
            resolve_schema("invoice", _schema())
        with self.assertRaisesRegex(ValueError, "template name or a JSON Schema"):
            resolve_schema(None, None)

    def test_unknown_template_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown template"):
            resolve_schema("w2", None)

    def test_invalid_custom_schema_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid JSON Schema"):
            resolve_schema(None, {"type": "not-a-type"})

    def test_builtin_schemas_are_valid(self):
        for name, entry in TEMPLATES.items():
            jsonschema.Draft202012Validator.check_schema(entry["schema"])


class TestFenceStripping(unittest.TestCase):
    def test_various_shapes(self):
        self.assertEqual(_strip_fences('{"a": 1}'), '{"a": 1}')
        self.assertEqual(_strip_fences('```json\n{"a": 1}\n```'), '{"a": 1}')
        self.assertEqual(_strip_fences('```\n{"a": 1}\n```'), '{"a": 1}')


class TestBuildMessages(unittest.TestCase):
    def test_excerpts_are_numbered_with_locations(self):
        messages = build_messages(_schema(), _excerpts("Invoice INV-1 total 100", "Page two text"))
        user_content = messages[-1]["content"]
        self.assertIn("[1] (invoice.pdf, Page 1)", user_content)
        self.assertIn("[2] (invoice.pdf, Page 1)", user_content)


class TestRunExtractionCore(unittest.IsolatedAsyncioTestCase):
    async def test_valid_output(self):
        async def ask(messages):
            return _payload({"invoice_number": "INV-1", "total_amount": 100}, "high",
                            {"invoice_number": 1})

        result = await run_extraction_core(_schema(), _excerpts("Invoice INV-1 total 100"), ask)
        self.assertEqual(result.data["invoice_number"], "INV-1")
        self.assertEqual(result.confidence, "high")
        self.assertEqual(result.citations[0]["field"], "invoice_number")
        self.assertEqual(result.citations[0]["document_name"], "invoice.pdf")

    async def test_invalid_then_fixed_succeeds(self):
        calls: list[str] = []

        async def ask(messages):
            calls.append(messages[-1]["content"])
            if len(calls) == 1:
                return _payload({"invoice_number": "INV-1", "total_amount": "not-a-number"})
            return _payload({"invoice_number": "INV-1", "total_amount": 100})

        result = await run_extraction_core(_schema(), _excerpts("x"), ask)
        self.assertEqual(result.data["total_amount"], 100)
        self.assertEqual(len(calls), 2)
        self.assertIn("previous response was invalid", calls[1])

    async def test_both_attempts_invalid_raises(self):
        async def ask(messages):
            return _payload({"invoice_number": 12345, "total_amount": "wrong"})

        with self.assertRaises(ExtractionError):
            await run_extraction_core(_schema(), _excerpts("x"), ask)

    async def test_non_json_output_raises_after_retry(self):
        async def ask(messages):
            return "I cannot help with that."

        with self.assertRaises(ExtractionError):
            await run_extraction_core(_schema(), _excerpts("x"), ask)

    async def test_bad_confidence_defaults_to_low(self):
        async def ask(messages):
            return _payload({"invoice_number": None, "total_amount": None}, "certain")

        result = await run_extraction_core(_schema(), _excerpts("x"), ask)
        self.assertEqual(result.confidence, "low")

    async def test_unknown_excerpt_numbers_are_skipped(self):
        async def ask(messages):
            return _payload({"invoice_number": "INV-1"}, "high", {"invoice_number": 99})

        result = await run_extraction_core(_schema(), _excerpts("x"), ask)
        self.assertEqual(result.citations, [])


if __name__ == "__main__":
    unittest.main()
