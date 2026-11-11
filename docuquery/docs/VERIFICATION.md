# Verification & tuning guide

New configuration introduced by the grounding/retrieval/citation upgrade, with
defaults and recommended values for two deployment shapes. Everything also
lives in `backend/.env.example`.

## New environment variables

| Variable | Default | Purpose |
|---|---|---|
| `GROUNDING_MODE` | `soft` | `strict` refuses unless every citation verifies; `soft` keeps supported sentences |
| `GROUNDING_AUTO_ATTRIBUTION` | `True` | Attach citations to marker-less drafts by embedding similarity |
| `GROUNDING_SIMILARITY_THRESHOLD` | `0.55` | Cosine floor for auto-attribution |
| `GROUNDING_FUZZY_QUOTES` | `True` | Tolerant quote matching (rapidfuzz) |
| `GROUNDING_QUOTE_THRESHOLD` | `85` | Fuzzy score floor (0–100) |
| `VERIFIER_TIMEOUT_SECONDS` | `30` | Verifier LLM timeout |
| `VERIFIER_MODEL` / `REWRITER_MODEL` | (LLM_MODEL) | Separate models for verification / follow-up rewriting |
| `LOG_DRAFT_ANSWERS` | True local / False production | Store the raw draft in `query_logs.draft_answer` |
| `PROMPT_BUDGET_ENABLED` | `True` | Drop lowest-ranked excerpts to fit the context budget |
| `HISTORY_LIMIT` / `HISTORY_MAX_CHARS` | `4` / `800` | Conversation history bound |
| `LLM_CONTEXT_BUDGET_TOKENS` | `6000` | Prompt budget (must fit the model's num_ctx) |
| `OLLAMA_CONTEXT_CHECK` | `True` | Startup warning when num_ctx < budget |
| `ANSWER_INCLUDE_REWRITTEN_QUESTION` | `True` | Pass the standalone question to the answer prompt |
| `MIN_RELEVANCE` | `0.0` (off) | Refuse with `low_relevance` below the floor |
| `NEIGHBOR_EXPANSION` / `NEIGHBOR_WINDOW` | `True` / `1` | Attach adjacent chunks as uncited context |
| `KEYWORD_WEBSEARCH_TSQUERY` | `True` | Stop-word-free websearch_to_tsquery keyword leg |
| `RERANKER_CANDIDATES` | `20` | Candidate pool size when reranking |
| `EMBED_DOC_PREFIX` / `EMBED_QUERY_PREFIX` | `"search_document: "` / `"search_query: "` | nomic task prompts; empty for other models |
| `EMBEDDING_VERSION_TAG` | `prefix-v1` | Version tag stored on chunks |
| `CONTEXTUAL_EMBEDDINGS` | `True` | "Document: name \| location" embedder header |
| `PDF_EXTRACTOR` | `plain` | `markdown` = pymupdf4llm layout-aware extraction |

## Recommended profiles

### (a) Local, qwen2.5:3b (default setup)

```env
LLM_MODEL=docuquery           # created from ollama/Modelfile.docuquery (num_ctx 8192)
GROUNDING_MODE=soft
GROUNDING_AUTO_ATTRIBUTION=True
GROUNDING_SIMILARITY_THRESHOLD=0.5   # 3B embeddings+verifier are noisier; keep the floor low
MIN_RELEVANCE=0.0                    # off — a 3B pipeline ranks less predictably
NEIGHBOR_EXPANSION=True
EMBED_DOC_PREFIX="search_document: "
EMBED_QUERY_PREFIX="search_query: "
CONTEXTUAL_EMBEDDINGS=True
PDF_EXTRACTOR=plain
# VERIFIER_MODEL / REWRITER_MODEL omitted -> same 3B model everywhere
```
After changing any EMBED_*/CONTEXTUAL value: `python scripts/reindex_embeddings.py`.

### (b) Stronger model (qwen2.5:14b local, or a cloud model via LLM_BASE_URL)

```env
LLM_MODEL=qwen2.5:14b
VERIFIER_MODEL=qwen2.5:14b           # verification is the quality bottleneck
REWRITER_MODEL=qwen2.5:3b            # rewriting stays cheap
LLM_CONTEXT_BUDGET_TOKENS=8000
GROUNDING_SIMILARITY_THRESHOLD=0.55
MIN_RELEVANCE=0.25                   # with the reranker on (sigmoid scores); 0.30 on raw cosine
RERANKER_ENABLED=True
RERANKER_MODEL=BAAI/bge-reranker-base
PDF_EXTRACTOR=markdown               # digital PDFs with headings/tables
```
Cloud models (Z.ai/OpenAI-compatible): also set `LLM_BASE_URL`/`LLM_API_KEY`,
empty the embed prefixes unless the provider's model expects them, and raise
`LLM_CONTEXT_BUDGET_TOKENS` to what the provider allows.

## Manual verification checklist

1. **Start the stack** — Postgres + Redis up (`docker compose up db redis -d`),
   Ollama running with the DocuQuery model:
   `ollama create docuquery -f ollama/Modelfile.docuquery` then set
   `LLM_MODEL=docuquery`. Start the API (`cd backend && python run.py`) — the
   log must show no `reason=ollama_context_too_small` warning.
2. **Migrations applied** — `alembic upgrade head` reaches `0008`
   (`scripts/verify_migrations.py` rehearses from scratch).
3. **Upload one PDF** with a known fact (e.g. "Annual leave is 25 days per
   year"). Documents page shows *Ready* (a green badge; if embedding failed,
   a *keyword-only* badge with a Re-embed button appears instead).
4. **Ask 5 questions**: 3 the PDF answers (one multi-part, one paraphrase, one
   follow-up like "and what about rollover?"), 2 it cannot answer (one
   off-topic fact, one related-but-absent topic).
   Expect: the 3 answerable ones answer with numbered citations (no refusal);
   the 2 unanswerable ones refuse.
5. **Check diagnostics**:
   ```sql
   SELECT question, refusal_reason, prompt_chars, length(draft_answer)
   FROM query_logs ORDER BY created_at DESC LIMIT 5;
   ```
   Answerable rows: `refusal_reason='ok'`; unanswerable: `model_refused` or
   `verifier_rejected` — never `verifier_error`/`pipeline_error` in a healthy
   setup.
6. **Citation inspector** — click a citation number in an answer: the modal
   highlights the supporting quote inside the chunk and offers *Open page N*
   (opens the PDF at that page).
7. **Re-run the eval** to compare profiles on your own PDF:
   ```bash
   cp scripts/evals/e2e_dataset.template.json scripts/evals/my_dataset.json
   # fill in 5 answerable + 3 unanswerable questions for your PDF
   python scripts/eval_e2e.py --dataset scripts/evals/my_dataset.json \
       --email you@example.com --password YourPassword --profile grounded
   python scripts/eval_e2e.py --dataset scripts/evals/my_dataset.json \
       --email you@example.com --password YourPassword --profile baseline
   ```
   Compare `false_refusal_rate_pct` (want ≈0), `correct_refusal_rate_pct`
   (want 100), `answer_keyword_recall_pct`, `citation_page_hit_rate_pct`,
   the refusal histogram and p50/p95 in `docs/eval_reports/`.
