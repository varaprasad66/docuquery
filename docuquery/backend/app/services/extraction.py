"""Structured extraction over ingested documents (the automation feature).

Pipeline: gather each document's chunks as numbered excerpts -> ask the LLM for
JSON matching a schema -> validate with jsonschema -> retry once feeding the
validation error back -> return the data with per-field source citations and the
model's self-assessed confidence.

The core is DB-free: callers resolve document authorization and chunk loading
(API: tenant-scoped; worker: by job), then hand over excerpts and an `ask_json`
callable so both async (API) and sync (worker) paths share one implementation.
"""

import json
import logging
from dataclasses import dataclass, field

import jsonschema

logger = logging.getLogger(__name__)

MAX_SCHEMA_CHARS = 4000
MAX_EXCERPT_CHARS = 2500


class ExtractionError(ValueError):
    """The LLM could not produce schema-valid output (after one retry)."""


@dataclass
class Excerpt:
    index: int  # 1-based excerpt number shown to the model
    document_name: str
    location: str
    content: str

    def render(self) -> str:
        return f"[{self.index}] ({self.document_name}, {self.location})\n{self.content[:MAX_EXCERPT_CHARS]}"


@dataclass
class ExtractionResult:
    data: dict
    confidence: str
    citations: list = field(default_factory=list)  # [{field, excerpt, document_name, location}]
    template: str = "custom"


# --- Built-in templates (professional edition ships these two) ----------------

INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_number": {"type": ["string", "null"], "description": "Invoice identifier"},
        "vendor_name": {"type": ["string", "null"], "description": "Company that issued the invoice"},
        "invoice_date": {"type": ["string", "null"], "description": "ISO date (YYYY-MM-DD) if present"},
        "due_date": {"type": ["string", "null"], "description": "ISO date (YYYY-MM-DD) if present"},
        "currency": {"type": ["string", "null"], "description": "ISO 4217 code, e.g. USD"},
        "total_amount": {"type": ["number", "null"], "description": "Total amount due"},
        "line_items": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": ["string", "null"]},
                    "quantity": {"type": ["number", "null"]},
                    "amount": {"type": ["number", "null"]},
                },
            },
        },
    },
}

CONTRACT_CLAUSES_SCHEMA = {
    "type": "object",
    "properties": {
        "parties": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "Legal names of the contracting parties",
        },
        "effective_date": {"type": ["string", "null"], "description": "ISO date (YYYY-MM-DD) if present"},
        "termination_clause": {"type": ["string", "null"], "description": "Summary of termination terms"},
        "payment_terms": {"type": ["string", "null"], "description": "Summary of payment terms"},
        "governing_law": {"type": ["string", "null"], "description": "Jurisdiction named in the contract"},
        "renewal_terms": {"type": ["string", "null"], "description": "Auto-renewal or extension terms"},
    },
}

TEMPLATES: dict[str, dict] = {
    "invoice": {
        "description": "Invoice header fields, totals and line items.",
        "schema": INVOICE_SCHEMA,
    },
    "contract_clauses": {
        "description": "Contract key terms: parties, dates, termination, payment, governing law.",
        "schema": CONTRACT_CLAUSES_SCHEMA,
    },
}


_EXTRACTION_SYSTEM = """You extract structured data from document excerpts.

Return ONLY one JSON object with exactly these keys and nothing else:
  "data": an object matching the requested JSON Schema (null for unknown fields)
  "confidence": "high" | "medium" | "low" for the extraction overall
  "field_sources": {"<field or path>": <excerpt number the value came from>}

No markdown fences, no commentary. If the excerpts do not contain a field, use
null. Numbers must be JSON numbers, dates ISO (YYYY-MM-DD).
"""


def resolve_schema(template: str | None, json_schema: dict | None) -> tuple[str, dict]:
    """Resolve the request into (template_name, schema). Exactly one must be given."""
    if template and json_schema:
        raise ValueError("Provide either a template or a JSON Schema, not both.")
    if template:
        entry = TEMPLATES.get(template)
        if entry is None:
            raise ValueError(
                f"Unknown template '{template}'. Available: {', '.join(sorted(TEMPLATES))}."
            )
        return template, entry["schema"]
    if json_schema:
        if len(json.dumps(json_schema)) > MAX_SCHEMA_CHARS:
            raise ValueError(f"JSON Schema exceeds {MAX_SCHEMA_CHARS} characters.")
        try:
            jsonschema.Draft202012Validator.check_schema(json_schema)
        except jsonschema.SchemaError as exc:
            raise ValueError(f"Invalid JSON Schema: {exc.message}") from exc
        return "custom", json_schema
    raise ValueError("Provide a template name or a JSON Schema.")


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _parse_llm_payload(raw: str) -> dict:
    try:
        payload = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Response was not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict) or "data" not in payload:
        raise ValueError("Response JSON must be an object with a 'data' key.")
    return payload


def build_messages(schema: dict, excerpts: list[Excerpt]) -> list[dict]:
    excerpt_block = "\n\n".join(e.render() for e in excerpts) or "(no excerpts)"
    return [
        {"role": "system", "content": _EXTRACTION_SYSTEM},
        {
            "role": "user",
            "content": f"JSON SCHEMA:\n{json.dumps(schema)}\n\nDOCUMENT EXCERPTS:\n{excerpt_block}",
        },
    ]


async def run_extraction_core(
    schema: dict,
    excerpts: list[Excerpt],
    ask_json,
    template: str = "custom",
) -> ExtractionResult:
    """Schema-validate the model's JSON, retrying once with the validation error.

    `ask_json(messages) -> str` is injected so API (async client) and worker
    (sync client) paths share this implementation.
    """
    messages = build_messages(schema, excerpts)
    payload = None
    last_error = ""

    for attempt in range(2):
        if attempt == 1:
            messages = messages + [
                {
                    "role": "user",
                    "content": f"Your previous response was invalid: {last_error}\n"
                    "Return the corrected JSON object only.",
                }
            ]
        raw = await ask_json(messages)
        try:
            payload = _parse_llm_payload(raw)
            jsonschema.validate(instance=payload["data"], schema=schema)
            break
        except (ValueError, jsonschema.ValidationError) as exc:
            last_error = getattr(exc, "message", None) or str(exc)
            payload = None

    if payload is None:
        raise ExtractionError(f"Model output failed schema validation after retry: {last_error}")

    citations = []
    field_sources = payload.get("field_sources") or {}
    for field_name, excerpt_no in field_sources.items():
        try:
            excerpt = excerpts[int(excerpt_no) - 1]
        except (ValueError, TypeError, IndexError):
            continue
        citations.append(
            {
                "field": str(field_name)[:120],
                "excerpt": excerpt.index,
                "document_name": excerpt.document_name,
                "location": excerpt.location,
            }
        )

    confidence = payload.get("confidence")
    return ExtractionResult(
        data=payload["data"],
        confidence=confidence if confidence in ("high", "medium", "low") else "low",
        citations=citations,
        template=template,
    )
