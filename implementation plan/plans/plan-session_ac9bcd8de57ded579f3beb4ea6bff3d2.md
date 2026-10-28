# DocuQuery — Production-Grade Document Intelligence (RAG + OCR)

A fully local, privacy-first document intelligence platform: upload documents (including scanned PDFs), and chat across them with hybrid search, streaming answers, and page-level citations. Built to be deployed as a clickable portfolio demo.

## Stack (all decisions made)

| Layer | Choice | Why |
|---|---|---|
| LLM | **Qwen 2.5 3B via Ollama** (OpenAI-compatible API) | Free, local, private; swappable to Z.ai/OpenAI later via `.env` only |
| Embeddings | **nomic-embed-text via Ollama** | Same local story, no key needed |
| Vector DB | **Postgres 16 + pgvector** (Docker) | One DB for vectors + metadata + access control + chat history = strongest enterprise story |
| Backend | **FastAPI + SQLAlchemy 2 + Alembic** | Industry standard for AI services |
| Background jobs | **Celery + Redis** | "Production" signal; handles slow ingestion/OCR off the request path |
| OCR | **Tesseract (pytesseract)** + PyMuPDF for digital PDFs | Reliable on Windows; Azure Document Intelligence adapter can be added later |
| Frontend | **React + Vite + TypeScript + Tailwind** | Your choice; clean demo UI |
| Evals | Custom golden-set harness (retrieval hit-rate + LLM-graded answers) | The differentiator that closes deals |

## Repo layout

```
docuquery/
├── backend/app/
│   ├── api/            # routers: documents, chat, search, stats, health
│   ├── services/       # llm, embeddings, ingestion, chunking, retrieval, ocr
│   ├── workers/        # celery tasks (process_document)
│   ├── db/             # models (documents, chunks, conversations, query_logs)
│   ├── evals/          # golden Q&A set + harness
│   └── config.py       # pydantic-settings, provider-agnostic LLM config
├── frontend/src/       # pages: Documents (upload+list), Chat (streaming+citations)
├── sample_data/        # ~15 generated PDFs: contracts/HR/finance, digital + scanned
├── scripts/            # seed_corpus.py, run_eval.py
├── docker-compose.yml  # postgres+pgvector, redis, backend, worker, frontend
└── README.md           # written as a client-facing case study
```

## Production-grade features (what makes this portfolio-worthy)

- **Hybrid retrieval**: Postgres full-text (BM25-style) + pgvector semantic search, fused with reciprocal rank fusion — BM25-only mode works so we can develop before models are pulled
- **OCR pipeline**: scanned PDFs detected (low text density) → rasterized → Tesseract OCR → same chunk path
- **Citations**: every answer shows inline [1][2] markers + source panel with document name and page number
- **Streaming chat**: SSE token streaming, conversation history persisted
- **Ingestion status**: upload → processing → ready/failed with live progress (polling)
- **Token/cost + latency tracking** per query, exposed via a stats endpoint
- **JWT auth** with per-document ownership (multi-tenant-ready schema)
- **Eval harness**: 20+ golden questions over the sample corpus; hit-rate + faithfulness scores printed and kept in README
- **pytest suite** (API contracts, chunker, rank fusion) + Dockerized one-command deploy (`docker compose up`)

## Build phases

1. **Foundations** — verify environment (Docker daemon, Ollama, Python 3.14 wheel quirks; PyMuPDF fallback = pypdf), install missing tools, scaffold repo, docker-compose (postgres+pgvector, redis), config, health check
2. **Ingestion pipeline** — upload API, Celery worker, PDF parse → OCR fallback → heading-aware chunking → embeddings → pgvector; document list + status UI
3. **Retrieval + citations** — hybrid search + RRF, citation metadata, search API
4. **Chat** — streaming SSE endpoint with cited answers, chat UI, history
5. **Auth + hardening** — JWT, ownership checks, rate limiting, error handling
6. **Evals + ship** — golden set, eval script, stats page, sample corpus generation, Dockerfile(s), README case study

## Tool installs I'll need during Phase 1 (will confirm each)

- **Ollama** (winget) + pull `qwen2.5:3b` (~2 GB) and `nomic-embed-text` (~270 MB)
- **Tesseract OCR** (winget) for the OCR path
- pip/npm project dependencies

## What you provide

Nothing until Phase 4 testing — Ollama runs keyless. When you later want cloud quality, we change 3 lines in `.env`. A future upgrade to `qwen2.5:7b`/`14b` (better answers, more RAM) is likewise a one-line change.