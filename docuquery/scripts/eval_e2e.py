#!/usr/bin/env python3
"""End-to-end RAG evaluation: drives the REAL /chat pipeline for every case in
a dataset file and reports refusal quality, keyword recall, citation page hits,
latency and the refusal-reason histogram.

Unlike scripts/run_eval.py (retrieval-only golden eval, unchanged), this runs
the full stack in-process: retrieval -> prompt build -> LLM streaming ->
grounding verification -> SSE events, against the configured LLM/embeddings
(Ollama or cloud). Requires the corpus to be visible to the eval account.

Usage:
    python scripts/eval_e2e.py --dataset scripts/evals/e2e_dataset.template.json \
        --email me@example.com --password MyPassword123 --profile grounded

    # compare against the old behavior in one run:
    python scripts/eval_e2e.py ... --profile baseline

Profiles set env overrides BEFORE the app is imported (env beats .env). Any
--set KEY=VALUE pair overrides both. The markdown report lands in
docs/eval_reports/<timestamp>.md alongside a JSON summary.

Secrets (password, API keys) are never printed or written to reports; reports
carry reason codes, scores, page numbers, chunk ids and latencies only.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
REPORT_DIR = ROOT / "docs" / "eval_reports"
sys.path.insert(0, str(BACKEND_DIR))

REFUSAL_TEXT = "I don't have enough information in the uploaded documents to answer that."

# Named config profiles so baseline vs new settings are directly comparable.
# "grounded" deliberately leaves MIN_RELEVANCE / embed prefixes / PDF_EXTRACTOR
# at whatever the operator configured in .env.
PROFILES: dict[str, dict[str, str]] = {
    "baseline": {
        "GROUNDING_MODE": "strict",
        "GROUNDING_AUTO_ATTRIBUTION": "False",
        "GROUNDING_FUZZY_QUOTES": "False",
        "PROMPT_BUDGET_ENABLED": "False",
        "HISTORY_LIMIT": "30",
        "NEIGHBOR_EXPANSION": "False",
        "MIN_RELEVANCE": "0.0",
        "KEYWORD_WEBSEARCH_TSQUERY": "False",
        "EMBED_DOC_PREFIX": "",
        "EMBED_QUERY_PREFIX": "",
        "CONTEXTUAL_EMBEDDINGS": "False",
        "QUERY_REWRITE_ENABLED": "False",
        "PDF_EXTRACTOR": "plain",
    },
    "grounded": {
        "GROUNDING_MODE": "soft",
        "GROUNDING_AUTO_ATTRIBUTION": "True",
        "GROUNDING_FUZZY_QUOTES": "True",
        "PROMPT_BUDGET_ENABLED": "True",
        "NEIGHBOR_EXPANSION": "True",
        "KEYWORD_WEBSEARCH_TSQUERY": "True",
        "CONTEXTUAL_EMBEDDINGS": "True",
        "QUERY_REWRITE_ENABLED": "True",
    },
}


def apply_overrides(profile: str | None, extra: list[str]) -> dict:
    applied: dict[str, str] = {}
    if profile:
        if profile not in PROFILES:
            raise SystemExit(f"Unknown profile {profile!r}; choose from {sorted(PROFILES)}")
        applied.update(PROFILES[profile])
    for pair in extra:
        key, _, value = pair.partition("=")
        if not key:
            raise SystemExit(f"--set expects KEY=VALUE, got {pair!r}")
        applied[key.strip().upper()] = value
    for key, value in applied.items():
        os.environ[key] = value
    return applied


def parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[len("data: "):]))
            except json.JSONDecodeError:
                continue
    return events


def percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    index = max(0, min(len(sorted_values) - 1, round(q * len(sorted_values) - 1)))
    return round(sorted_values[index], 1)


async def ensure_account(client, email: str, password: str) -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    if response.status_code == 200:
        return response.json()["access_token"]
    response = await client.post("/api/auth/register", json={"email": email, "password": password})
    if response.status_code == 201:
        response = await client.post("/api/auth/login", json={"email": email, "password": password})
        if response.status_code == 200:
            return response.json()["access_token"]
    raise SystemExit("Could not log in or register the eval account (check EMAIL/PASSWORD).")


async def upload_corpus(client, token: str, corpus_dir: Path) -> list[str]:
    """Upload PDFs from a directory once (skip names already indexed) and wait
    until every document is ready. Returns the document ids."""
    headers = {"Authorization": f"Bearer {token}"}
    listed = (await client.get("/api/documents", headers=headers))
    if listed.status_code != 200 or not isinstance(listed.json(), list):
        raise SystemExit(f"Could not list documents for the eval account: {listed.status_code} {listed.text[:200]}")
    existing = {doc["original_name"]: doc for doc in listed.json()}
    ids: list[str] = []
    for path in sorted(corpus_dir.glob("*.pdf")):
        doc = existing.get(path.name)
        if doc and doc["status"] == "ready":
            ids.append(doc["id"])
            continue
        print(f"  uploading {path.name} ...")
        response = await client.post(
            "/api/documents",
            files={"file": (path.name, path.read_bytes(), "application/pdf")},
            headers=headers,
        )
        if response.status_code != 201:
            print(f"  upload failed for {path.name}: {response.text[:200]}")
            continue
        doc_id = response.json()["id"]
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            detail = (await client.get(f"/api/documents/{doc_id}", headers=headers)).json()
            if detail["status"] in ("ready", "failed"):
                break
            await asyncio.sleep(1.5)
        if detail["status"] == "ready":
            ids.append(doc_id)
        else:
            print(f"  document failed to process: {path.name} ({detail.get('error')})")
    return ids


async def run_case(client, token: str, case: dict, document_ids: list[str] | None, top_k: int) -> dict:
    payload: dict = {"question": case["question"], "top_k": top_k}
    if document_ids:
        payload["document_ids"] = document_ids
    started = time.perf_counter()
    response = await client.post(
        "/api/chat", json=payload, headers={"Authorization": f"Bearer {token}"}
    )
    wall_ms = round((time.perf_counter() - started) * 1000)

    events = parse_sse(response.text)
    answer = "".join(e.get("value", "") for e in events if e.get("type") == "token")
    done = next((e for e in events if e.get("type") == "done"), {})
    citations = next((e.get("citations", []) for e in events if e.get("type") == "citations"), [])

    reason = done.get("refusal_reason")
    if not reason:
        if not done:
            reason = "pipeline_error"  # stream died before the done event
        elif REFUSAL_TEXT in answer:
            reason = "model_refused"
        else:
            reason = "ok"
    refused = reason != "ok"

    expected_keywords = [k for k in case.get("expected_keywords", [])]
    keywords_hit = [k for k in expected_keywords if k.casefold() in answer.casefold()]
    expected_pages = set(case.get("expected_pages", []))
    cited_pages = sorted({c.get("page") for c in citations if isinstance(c.get("page"), int)})
    page_hit = bool(expected_pages & set(cited_pages)) if case.get("answerable") else None

    return {
        "id": case.get("id", "?"),
        "question": case["question"],
        "answerable": bool(case.get("answerable", True)),
        "refused": refused,
        "reason": reason,
        "unverified": bool(done.get("unverified")),
        "keywords_hit": keywords_hit,
        "keywords_total": len(expected_keywords),
        "cited_pages": cited_pages,
        "page_hit": page_hit,
        "wall_ms": wall_ms,
        "latency_ms": done.get("latency_ms", wall_ms),
        "prompt_tokens": done.get("prompt_tokens"),
        "completion_tokens": done.get("completion_tokens"),
        "chunk_ids": [c.get("chunk_id") for c in citations],
    }


def summarize(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    answered = [r for r in answerable if not r["refused"]]
    latencies = sorted(float(r["wall_ms"]) for r in rows)
    keyword_scores = [
        len(r["keywords_hit"]) / r["keywords_total"]
        for r in answered
        if r["keywords_total"]
    ]
    page_checks = [r for r in answerable if r["page_hit"] is not None]
    return {
        "total_cases": len(rows),
        "answerable_cases": len(answerable),
        "unanswerable_cases": len(unanswerable),
        "false_refusal_rate_pct": round(
            sum(1 for r in answerable if r["refused"]) / len(answerable) * 100, 1
        ) if answerable else 0.0,
        "correct_refusal_rate_pct": round(
            sum(1 for r in unanswerable if r["refused"]) / len(unanswerable) * 100, 1
        ) if unanswerable else 0.0,
        "answer_keyword_recall_pct": round(
            statistics.mean(keyword_scores) * 100, 1
        ) if keyword_scores else 0.0,
        "citation_page_hit_rate_pct": round(
            sum(1 for r in page_checks if r["page_hit"]) / len(page_checks) * 100, 1
        ) if page_checks else 0.0,
        "unverified_answers": sum(1 for r in rows if r["unverified"]),
        "refusal_reasons": dict(Counter(r["reason"] for r in rows)),
        "latency_ms_p50": percentile(latencies, 0.50),
        "latency_ms_p95": percentile(latencies, 0.95),
    }


def write_report(args, applied: dict, rows: list[dict], summary: dict, config_snapshot: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_path = REPORT_DIR / f"{stamp}-{args.profile or 'custom'}.md"
    json_path = report_path.with_suffix(".json")

    lines = [
        "# DocuQuery end-to-end eval report",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Profile: `{args.profile or 'custom'}`",
        f"- Dataset: `{args.dataset}`",
        f"- Overrides applied: `{json.dumps(applied, sort_keys=True)}`" if applied else "- Overrides: none",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
    ]
    for key, value in summary.items():
        if key != "refusal_reasons":
            lines.append(f"| {key} | {value} |")
    lines += ["", "## Refusal reason histogram", ""]
    for reason, count in sorted(summary["refusal_reasons"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{reason}` | {count} |")
    lines += [
        "",
        "## Per-case detail",
        "",
        "| id | answerable | refused | reason | keywords | cited pages | page hit | unverified | latency ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {'Y' if r['answerable'] else 'N'} | {'Y' if r['refused'] else 'N'} "
            f"| `{r['reason']}` | {len(r['keywords_hit'])}/{r['keywords_total']} "
            f"| {r['cited_pages'] or '—'} | {'Y' if r['page_hit'] else ('N' if r['page_hit'] is False else '—')} "
            f"| {'Y' if r['unverified'] else 'N'} | {r['wall_ms']} |"
        )
    lines += [
        "",
        "## Config snapshot (no secrets)",
        "",
        "```",
    ]
    for key, value in sorted(config_snapshot.items()):
        lines.append(f"{key}={value}")
    lines += ["```", "", "Note: when query-side embed prefixes are enabled, run "
              "`python scripts/reindex_embeddings.py` first if the corpus was "
              "ingested before prefixes existed, otherwise vector recall is depressed.", ""]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(json.dumps({"summary": summary, "cases": rows}, indent=2), encoding="utf-8")
    return report_path


async def run(args, applied: dict) -> int:
    # Imports happen AFTER env overrides are applied (see main).
    import httpx

    from app.config import get_settings
    from app.main import app

    settings = get_settings()
    config_snapshot = {
        "LLM_MODEL": settings.llm_model,
        "GROUNDING_MODE": settings.grounding_mode,
        "GROUNDING_AUTO_ATTRIBUTION": settings.grounding_auto_attribution,
        "GROUNDING_FUZZY_QUOTES": settings.grounding_fuzzy_quotes,
        "MIN_RELEVANCE": settings.min_relevance,
        "NEIGHBOR_EXPANSION": settings.neighbor_expansion,
        "RERANKER_ENABLED": settings.reranker_enabled,
        "EMBEDDINGS_MODEL": settings.embeddings_model,
        "EMBED_DOC_PREFIX_SET": bool(settings.embed_doc_prefix),
        "EMBED_QUERY_PREFIX_SET": bool(settings.embed_query_prefix),
        "CONTEXTUAL_EMBEDDINGS": settings.contextual_embeddings,
        "KEYWORD_WEBSEARCH_TSQUERY": settings.keyword_websearch_tsquery,
        "PDF_EXTRACTOR": settings.pdf_extractor,
        "LLM_CONTEXT_BUDGET_TOKENS": settings.llm_context_budget_tokens,
        "QUERY_REWRITE_ENABLED": settings.query_rewrite_enabled,
        "TOP_K": args.top_k,
    }
    if settings.embed_query_prefix and settings.reranker_enabled is False:
        print("warning: query prefixes are set; re-run scripts/reindex_embeddings.py if the "
              "corpus was indexed without prefixes, else vector recall will be poor")

    document_ids: list[str] | None = None
    if args.document_ids:
        document_ids = [d.strip() for d in args.document_ids.split(",") if d.strip()]

    cases = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    if args.limit:
        cases = cases[: args.limit]

    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://evalserver") as client:
        token = await ensure_account(client, args.email, args.password)
        if args.corpus:
            ids = await upload_corpus(client, token, Path(args.corpus))
            document_ids = ids or document_ids
            print(f"corpus ready: {len(ids)} document(s)")

        rows: list[dict] = []
        print(f"\nRunning {len(cases)} cases through the real /chat pipeline...\n")
        for case in cases:
            row = await run_case(client, token, case, document_ids, args.top_k)
            rows.append(row)
            glyph = "REFUSED" if row["refused"] else "ok"
            print(
                f"[{glyph:>7}] {row['id']:<6} reason={row['reason']:<18} "
                f"keywords={len(row['keywords_hit'])}/{row['keywords_total']} "
                f"pages={row['cited_pages'] or '—'} latency={row['wall_ms']}ms"
            )

    summary = summarize(rows)
    print("\n===== E2E EVALUATION SUMMARY =====")
    for key, value in summary.items():
        if key != "refusal_reasons":
            print(f"{key}: {value}")
    print(f"refusal_reasons: {summary['refusal_reasons']}")

    report_path = write_report(args, applied, rows, summary, config_snapshot)
    print(f"\nreport written: {report_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="Path to the JSON dataset")
    parser.add_argument("--profile", choices=sorted(PROFILES), default=None)
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--email", default="eval@docuquery.local")
    parser.add_argument("--password", default="EvalPassword123")
    parser.add_argument("--corpus", default=None, help="Directory of PDFs to upload once before running")
    parser.add_argument("--document-ids", default=None, help="Comma-separated document ids to scope retrieval")
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    args = parser.parse_args()
    applied = apply_overrides(args.profile, args.overrides)
    # Psycopg async needs the selector loop on Windows; must be set before the
    # loop is created.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(run(args, applied))


if __name__ == "__main__":
    sys.exit(main())
