# Changelog

## 2026-11-11 — Grounding, retrieval quality & citation upgrade (phases 1–4)

Goal: stop false refusals on questions that ARE answered in the uploaded
documents, and improve answer quality + citations. Every new behavior is
behind a config flag with a safe default (see `.env.example`); schema changes
ship as Alembic migrations 0007 + 0008.

### Phase 1 — Observability + soft verification
- `verify_answer` returns a reason code; persisted in `query_logs.refusal_reason`
  (+ optional `draft_answer`, `prompt_chars`; `LOG_DRAFT_ANSWERS`, default on
  locally / off in production) and streamed in the SSE `done` event.
- `GROUNDING_MODE=strict|soft` (default soft): one bad citation no longer kills
  the whole answer — unsupported sentences are dropped, supported ones stay.
  Fuzzy quote matching via `rapidfuzz` (`GROUNDING_FUZZY_QUOTES`,
  `GROUNDING_QUOTE_THRESHOLD=85`).
- Automatic attribution for marker-less drafts
  (`GROUNDING_AUTO_ATTRIBUTION`, `GROUNDING_SIMILARITY_THRESHOLD=0.55`);
  answers ground this way are flagged `unverified` (badge in Chat.tsx).
- Prompt-size control: `HISTORY_LIMIT=4`, `HISTORY_MAX_CHARS=800`,
  `LLM_CONTEXT_BUDGET_TOKENS=6000` drop lowest-ranked excerpts first
  (`PROMPT_BUDGET_ENABLED`); Ollama context startup check (`OLLAMA_CONTEXT_CHECK`)
  + `ollama/Modelfile.docuquery` (num_ctx 8192) + docs/OLLAMA_SETUP.md.
- Separate models: `VERIFIER_MODEL`, `REWRITER_MODEL` (default to `LLM_MODEL`).
- Answer prompt carries the rewritten standalone question
  (`ANSWER_INCLUDE_REWRITTEN_QUESTION`).

### Phase 2 — Retrieval quality
- Embedding task prefixes `EMBED_DOC_PREFIX`/`EMBED_QUERY_PREFIX` for
  nomic-embed-text + `EMBEDDING_VERSION_TAG` recorded per chunk; resumable
  re-embedding without re-parsing: `scripts/reindex_embeddings.py`.
- Contextual embedder input (`CONTEXTUAL_EMBEDDINGS`) — stored text stays clean.
- Neighbor expansion (`NEIGHBOR_EXPANSION`, `NEIGHBOR_WINDOW=1`): prev/next
  chunk attached as uncited context, deduped, budget-capped.
- Reranker: 20-candidate pool (`RERANKER_CANDIDATES`), sigmoid-normalized
  scores, guaranteed keyword-only slot; `BAAI/bge-reranker-base` supported via
  `RERANKER_MODEL`.
- Relevance floor `MIN_RELEVANCE` (0 = off): below it the chat refuses with
  reason `low_relevance` instead of guessing.
- Keyword leg: stop words dropped, `websearch_to_tsquery`
  (`KEYWORD_WEBSEARCH_TSQUERY`), still fully parameterized.
- Degraded ingestion: `documents.degraded` (migration 0008) + keyword-only
  badge/Re-embed in Documents.tsx + `POST /api/documents/{id}/reembed`.
- Layout-aware extraction: `PDF_EXTRACTOR=markdown` (pymupdf4llm, heading-first
  chunking; plain/OCR remains the default).

### Phase 3 — Citations
- Citations carry `chunk_id`, `chunk_text` and best-effort quote char offsets
  (`quote_start`/`quote_end`); old messages stay valid (fields optional).
- `[n]` markers renumbered by order of first appearance in the final answer
  (SSE + persisted message).
- Citation inspector: quote highlighted inside the full chunk text, "Open page
  N" opens the source PDF at that page, unverified badge.
- Refusals and `unverified` answers are never cached.

### Phase 4 — Evaluation
- `scripts/eval_e2e.py`: end-to-end eval over a JSON dataset with
  false/correct refusal rates, keyword recall, citation page hits, refusal
  histogram, p50/p95 latency; `baseline`/`grounded` profiles; markdown + JSON
  reports in `docs/eval_reports/`. Template dataset with fill-in instructions:
  `scripts/evals/e2e_dataset.template.json`.
- Found + fixed by the harness: qwen2.5:3b intermittently answered with just
  `[1]` (prompt asked it to "cite as [n]" and repeated the question verbatim);
  prompt reworded, and marker-only drafts / refusal+markers now classify as
  `empty_draft` / `model_refused`.

### Verification
- 295 backend tests (unit + integration) pass; ruff clean; frontend `tsc`
  clean; Playwright e2e (`frontend/e2e/citations.spec.ts`) passes with mocked
  SSE; migrations rehearsed to 0008 on a fresh database
  (`scripts/verify_migrations.py`).
- Live check against a real PDF + Ollama (qwen2.5:3b): false_refusal_rate 0%,
  correct_refusal_rate 100%, keyword recall 100%, citation page hits 100%.
- Manual checklist: see docs/VERIFICATION.md.
