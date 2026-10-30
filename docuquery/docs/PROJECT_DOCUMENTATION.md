# DocuQuery — Project Documentation

**Local-First, Privacy-First RAG Document Intelligence Platform**

---

| | |
|---|---|
| **Product** | DocuQuery — Document Intelligence |
| **Version** | 0.3.0 (Enterprise edition feature-complete) |
| **Status** | Production-ready showcase — 108 automated tests green, live-demo verified |
| **Release date** | October 2026 |
| **Editions** | Starter · Professional · Enterprise (single codebase, edition-gated) |
| **License** | MIT |
| **Related documents** | [ARCHITECTURE.md](ARCHITECTURE.md) · [SECURITY_MODEL.md](SECURITY_MODEL.md) · [TESTING_STRATEGY.md](TESTING_STRATEGY.md) · [RUNBOOK.md](RUNBOOK.md) · [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) · [COMMERCIALIZATION.md](COMMERCIALIZATION.md) · [PRODUCT_OVERVIEW.md](PRODUCT_OVERVIEW.md) |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement & Solution](#2-problem-statement--solution)
3. [Implemented Capabilities](#3-implemented-capabilities)
4. [System Architecture](#4-system-architecture)
5. [Technology Stack](#5-technology-stack)
6. [API & Integration Catalog](#6-api--integration-catalog)
7. [Data Model](#7-data-model)
8. [Configuration Reference](#8-configuration-reference)
9. [Deployment & Operations](#9-deployment--operations)
10. [Quality Assurance & Benchmarks](#10-quality-assurance--benchmarks)
11. [Security & Privacy Model](#11-security--privacy-model)
12. [Current Limitations](#12-current-limitations)
13. [Future Scope & Roadmap](#13-future-scope--roadmap)
14. [Appendix A — Utility Scripts](#appendix-a--utility-scripts)
15. [Appendix B — Sample Corpus](#appendix-b--sample-corpus)
16. [Appendix C — Glossary](#appendix-c--glossary)

---

## 1. Executive Summary

DocuQuery is a **fully local, privacy-first document intelligence platform**. Organizations point it at their PDF corpus — contracts, policies, invoices, handbooks, research papers — and ask questions in plain language. DocuQuery returns **grounded answers with page-level citations**, and every byte of data stays inside the organization's own infrastructure: documents, embeddings, and language-model inference all run on-premise. No document content ever leaves the deployment.

The platform is a complete, production-grade system, not a prototype:

- **Ingestion pipeline** — PDF parsing with automatic OCR fallback for scanned pages, paragraph-aware chunking, and embedding generation, all tracked with live progress and retryable on failure.
- **Hybrid retrieval** — keyword search (Postgres full-text, GIN-indexed) and semantic vector search (pgvector HNSW) fused with Reciprocal Rank Fusion for measurably better recall than either method alone.
- **Streaming cited chat** — server-sent-events token streaming with inline `[1][2]` citations linked to source cards and a citation inspector showing the exact retrieved passage.
- **Enterprise security** — JWT authentication with rotating refresh tokens and theft detection, login lockout, sliding-window rate limiting, tenant isolation, an immutable audit trail, security headers, and error sanitization.
- **Commercially packaged** — three sellable editions (Starter, Professional, Enterprise) driven by a single `EDITION` environment variable over one codebase.
- **Verified quality** — 108 automated tests (56 unit + 52 integration), a 20-question golden evaluation harness with a 100% Top-6 retrieval hit rate, and a 35-check live demo script.

The default AI stack runs on **Ollama** (Qwen 2.5 3B for generation, nomic-embed-text for embeddings) so a full deployment costs **zero API fees**; any OpenAI-compatible provider (OpenAI, Z.ai GLM, Azure OpenAI, vLLM…) can be swapped in via environment variables.

---

## 2. Problem Statement & Solution

### 2.1 The problem

Enterprise knowledge is trapped in documents. Employees hunting for "what is the travel reimbursement limit?" or "which clause covers early termination?" must either read dozens of PDFs or ask a colleague. Generic cloud AI chatbots can't help because (a) they don't know the organization's documents and (b) uploading confidential documents to a third-party cloud is frequently prohibited by policy, contract, or regulation.

### 2.2 The solution

DocuQuery answers questions **strictly from the organization's own documents**, on the organization's own hardware:

| Pain point | DocuQuery's answer |
|---|---|
| "I don't know which of our 500 PDFs contains this." | Hybrid retrieval searches *all* ingested documents in milliseconds and ranks the best passages. |
| "I can't trust AI answers." | Every answer is generated *only* from retrieved excerpts and cites document name + page number inline as `[1][2]`, with the source passage inspectable in one click. |
| "We can't send documents to the cloud." | 100% local deployment: Postgres, Redis, the LLM, and the embeddings model all run inside the customer's network. Zero external calls by default. |
| "AI tools are expensive per question." | Local models mean zero per-token cost; a semantic cache answers repeated/similar questions without re-running the model at all. |
| "Scanned PDFs are useless." | Per-page OCR fallback (Tesseract at 200 DPI) automatically recovers text from scanned pages. |
| "Who did what?" | Every security-relevant action (login, upload, delete, visibility change, admin edit) is written to an audit trail. |

### 2.3 What "grounded" means in practice

The generation model receives a system prompt that instructs it to answer **strictly from numbered excerpts** and to reply with an exact, honest fallback sentence ("the answer is not in the provided documents") when the answer isn't present. It is never allowed to answer from its own world knowledge. This design — retrieval-augmented generation with citation enforcement — is what makes the output auditable and trustworthy for business use.

---

## 3. Implemented Capabilities

This chapter is the definitive list of what is **built, tested, and working today** (v0.3.0).

### 3.1 Document ingestion & processing

- **PDF upload with defense-in-depth validation** — extension check *and* `%PDF-` magic-byte sniffing (a renamed `.exe` is rejected), 50 MB size cap, empty-file rejection, configurable page cap (default 500).
- **Per-page OCR fallback** — each page is extracted with PyMuPDF; pages yielding fewer than 32 characters (scans, images) are rasterized at 200 DPI (grayscale, auto-upscaled for small pages) and OCR'd with Tesseract. The extraction method used for every page is recorded.
- **Paragraph-aware chunking** — text is split into ≤1,000-character chunks with 150-character overlap; oversized paragraphs are hard-split on sentence boundaries, and pathological content (URL walls, tables) is fixed-windowed. Token estimates are stored per chunk.
- **Live progress tracking** — a per-document progress value (0→1) is persisted at each pipeline stage and surfaced in the UI as an animated progress bar; the frontend polls every 1.5 s during processing.
- **Resilient indexing** — if embedding generation fails partway, remaining chunks are still indexed *without vectors* so keyword search keeps working; failed documents can be retried with one click (`POST /api/documents/{id}/retry`).
- **Background execution with two modes** — processing runs as a Celery task (Redis broker) when `USE_CELERY=true`, or transparently as an in-process FastAPI background task by default — same code path, zero broker dependency for small deployments.
- **Public/private visibility** — documents are private to their owner by default and can be marked public for team-wide access; guests always upload private.
- **Safe deletion** — deleting a document cascades its chunks, removes the stored file, and writes an audit event.

### 3.2 Hybrid retrieval engine

The core differentiator. DocuQuery runs **two independent retrieval legs and fuses them**:

| Leg | Technology | Strength |
|---|---|---|
| **Keyword (lexical)** | Postgres full-text search — `to_tsquery('english', …)` with OR semantics, ranked by `ts_rank_cd`, over a *generated stored* `tsvector` column with a **GIN index** | Exact terms, part numbers, names, codes |
| **Semantic (vector)** | pgvector cosine distance over a **HNSW-indexed** 768-dim `vector(768)` column (`1 - distance` scoring) | Meaning, synonyms, paraphrases |

- **Reciprocal Rank Fusion** (k = 60) merges both ranked lists; each result records which leg(s) matched it (`matched_by: ["vector"] / ["bm25"]`).
- Each leg contributes up to `top_k × 3` candidates before fusion, so the final `top_k` (default 6; configurable 1–20) is chosen from a much wider pool.
- **Tenant-scoped by construction** — a security clause is injected into every retrieval SQL statement: users see their own documents + public ones; guests see public documents + a shared guest space. There is no code path that retrieves across tenants.
- **Graceful degradation** — if the vector leg fails at runtime (e.g., embedding model offline), search automatically falls back to keyword-only instead of erroring.

### 3.3 Streaming cited chat

- **Server-Sent Events streaming** — answers stream token-by-token with a live cursor in the UI; the SSE event sequence is `meta → citations → token… → done` (or a sanitized `error`).
- **Page-level citations** — the model cites as `[n]`; the frontend renders each marker as a clickable button that opens a **Citation Inspector** modal showing the document name, page number, and the exact retrieved passage.
- **Multi-turn conversations** — the last 6 history messages (truncated to 2,000 chars) are included as context; conversations are created lazily and titled from the first question; full history, latency, and token stats are stored per message.
- **Semantic cache** — questions are embedded and matched against cached questions within the same tenant scope at cosine similarity ≥ 0.92 with a 60-minute TTL. On a hit, the stored answer and citations are replayed as a token stream and logged as `cached=true` — no LLM call at all. **The cache is invalidated wholesale whenever the corpus changes**, so a stale answer can never be served after an upload or delete.
- **Honest failure** — stream errors are sanitized into a generic user-safe message; details go to logs, not to the client.
- **User feedback** (Professional edition and up) — thumbs-up/down with optional comment on any assistant message.

### 3.4 Security & multi-user foundation

- **JWT authentication** — HS256 access tokens (7-day default) carrying `sub`/`email`/`jti`/`typ` claims; token `jti`s are blacklisted in Redis (with TTL) on logout so access tokens die immediately.
- **Rotating refresh tokens with theft detection** — 48-byte opaque tokens stored only as SHA-256 hashes, 30-day expiry, family-based rotation: refreshing revokes the presented token and issues a new one in the same family; **presenting an already-revoked token revokes the entire family** (replay attack mitigation).
- **Password policy** — minimum 10 characters with at least one letter and one digit; bcrypt hashing; timing-equalized invalid-login path to prevent user-enumeration timing attacks; generic error messages.
- **Login lockout** — 5 failed attempts per email/IP triggers a 15-minute lock (Redis-backed with in-memory fallback).
- **Sliding-window rate limiting** — per-user (or per-IP for guests) via Redis sorted sets, with a bounded in-process LRU fallback. Defaults per 60 s window: auth 10, chat 30, upload 20, search 60. Returns `429` with `Retry-After`.
- **Tenant isolation** — deny-by-default read/mutate checks; unauthorized access to another tenant's private document returns **404 (not 403)** so its existence is never leaked. Verified by a dedicated integration regression test.
- **Role-based administration** (Enterprise edition) — admin console for user management (activate/deactivate, role, display name), all-documents view, forced reprocessing, audit-trail browsing, and a global overview. Admins cannot deactivate themselves.
- **Immutable audit trail** — security-relevant actions (register, login, login_failed, upload, visibility, delete, admin edits) are recorded with actor, IP, user agent, resource, and JSON detail; configurable retention (default forever).
- **Hardened HTTP layer** — security headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, Referrer-Policy, Permissions-Policy), request-ID propagation, and error-sanitizing middleware that converts unhandled exceptions into generic 500s with a request ID.
- **Production boot guards** — the server refuses to start in `ENVIRONMENT=production` with the default JWT secret or `DEBUG=true`.
- **Anonymous-friendly demo mode** — invalid/expired tokens degrade to a guest identity (public documents only) rather than hard-failing, enabling zero-friction public demos without weakening logged-in security.

### 3.5 Analytics & observability

- **Scoped analytics dashboard** — admins see global metrics; users see their own; guests see guest scope. KPIs: documents indexed (total/ready/failed/pages), chunks, queries processed, average latency with **retrieval vs. generation split**, token usage with **estimated cost** (real per-model pricing when configured), cache hit rate, and feedback tallies.
- **14-day daily usage series** (Professional/Enterprise).
- **Recent query log** — question, model, retrieval/LLM/total latency, tokens, timestamp.
- **Health endpoint** — liveness plus component probes (database `SELECT 1`, LLM endpoint reachability) with per-component status, edition, environment, and latency; the frontend renders it as a live health pill, polled every 30 s.

### 3.6 Commercial editions (one codebase)

The `EDITION` environment variable (`basic` / `professional` / `enterprise`) gates surfaces at the API layer. Lower editions receive **404 (not 403)** for higher-tier surfaces, so the feature map is never revealed.

| Capability | Starter (basic) | Professional | Enterprise |
|---|---|---|---|
| Upload, OCR, hybrid retrieval, cited chat | ✅ | ✅ | ✅ |
| JWT auth, refresh rotation, lockout, rate limits, audit | ✅ | ✅ | ✅ |
| Message feedback (👍/👎) | — | ✅ | ✅ |
| Daily usage analytics series & quotas | — | ✅ | ✅ |
| Admin console (users, documents, audit, overview) | — | — | ✅ |

Security fundamentals ship in **every** edition. Edition gating is verified by a dedicated probe script (`scripts/edition_check.py`).

### 3.7 Model-agnostic AI layer

- Default stack: **Ollama** with `qwen2.5:3b` (temperature 0.2, max 1,024 tokens) and `nomic-embed-text` (768-dim) — zero API cost, fully offline.
- Every AI endpoint (generation, embeddings) is OpenAI-compatible and swappable via environment variables — OpenAI, Z.ai GLM, Azure OpenAI, vLLM, LM Studio, or any compatible gateway, without code changes.
- Embedding dimension is validated against `EMBED_DIM` at startup of each embedding call, failing loudly on a mismatch instead of silently corrupting the index.

---

## 4. System Architecture

### 4.1 Component overview

```mermaid
flowchart LR
    subgraph Client
        UI[React 19 SPA<br/>Documents · Chat · Analytics]
    end
    subgraph Application["Backend (FastAPI, port 8100)"]
        API[REST + SSE API layer<br/>auth · documents · search · chat · stats · admin]
        RL[Rate limiting + lockout<br/>Redis sliding window]
        SC[Semantic cache]
        RET[Hybrid retrieval<br/>RRF fusion]
        LLM[LLM client<br/>OpenAI-compatible]
        EMB[Embedding client<br/>OpenAI-compatible]
        BG[Background processing<br/>Celery task or in-process]
        OCR[PDF parser + OCR<br/>PyMuPDF + Tesseract]
    end
    subgraph Data["Data layer (all local)"]
        PG[(Postgres 16 + pgvector<br/>documents · chunks · vectors<br/>FTS · audit · cache)]
        RD[(Redis 7<br/>rate limits · lockout<br/>jti blacklist · Celery broker)]
        FS[(uploads/<br/>PDF files on disk)]
    end
    subgraph AI["Local AI (Ollama, port 11434)"]
        GEN[qwen2.5:3b<br/>generation]
        NEM[nomic-embed-text<br/>768-dim embeddings]
    end
    UI -->|HTTPS REST + SSE| API
    API --> RL --> RD
    API --> RET --> PG
    RET --> EMB --> NEM
    API --> SC --> PG
    API --> LLM --> GEN
    API --> BG --> OCR
    BG --> EMB
    BG --> PG
    API --> PG
    OCR --> FS
```

### 4.2 Ingestion pipeline (upload → searchable)

```mermaid
sequenceDiagram
    participant U as User (UI)
    participant A as API
    participant W as Worker (Celery / BackgroundTask)
    participant P as PDF+OCR parser
    participant E as Embeddings (Ollama)
    participant D as Postgres

    U->>A: POST /api/documents (PDF multipart)
    A->>A: magic-byte + size + quota validation
    A->>D: document row (status=processing, progress 0.01)
    A-->>U: 201 Created
    A->>W: dispatch process_document
    W->>P: extract pages (progress 0.05 → 0.35)
    P->>P: page < 32 chars ? OCR @ 200 DPI : direct text
    W->>W: chunk pages (1000 chars, 150 overlap) → 0.45
    loop batches of 10 chunks
        W->>E: embed batch (progress → 0.95)
        E-->>W: 768-dim vectors
    end
    W->>D: persist chunks (+tsvector, +embedding)
    W->>D: status=ready, progress=1.0
    W->>D: invalidate semantic cache (corpus changed)
```

Failure behavior: empty extraction → status `failed` with a user-readable error (retryable). Embedding failure mid-way → remaining chunks indexed without vectors (keyword search still works).

### 4.3 Query pipeline (question → cited answer)

```mermaid
sequenceDiagram
    participant U as User (UI)
    participant A as Chat API
    participant C as Semantic cache
    participant R as Hybrid retrieval
    participant L as LLM (Ollama)
    participant D as Postgres

    U->>A: POST /api/chat (question, SSE)
    A->>A: quota + rate-limit check, persist user message
    A->>C: embed question, lookup (cosine ≥ 0.92, TTL 60 min)
    alt cache hit
        C-->>A: stored answer + citations
        A-->>U: replay as token stream (cached=true)
    else cache miss
        A->>R: retrieve top_k (vector + BM25 → RRF)
        R-->>A: ranked chunks (tenant-scoped)
        A-->>U: SSE meta + citations events
        A->>L: stream completion (system prompt + context + history)
        L-->>A: token deltas
        A-->>U: SSE token events → done
        A->>D: persist assistant message + query log (latency, tokens, cost)
        A->>C: store fresh answer in cache
    end
```

### 4.4 Deployment topology

`docker-compose.yml` defines five services:

| Service | Image | Host port | Role |
|---|---|---|---|
| `db` | pgvector/pgvector:pg16 | 5433 | Postgres with pgvector; runs migrations via Alembic at backend startup |
| `redis` | redis:7-alpine | 6379 | Rate limits, lockout, token blacklist, Celery broker |
| `backend` | python:3.12-slim + Tesseract | 8100 | FastAPI (uvicorn); `alembic upgrade head` on boot |
| `worker` | (same image as backend) | — | Celery worker for document processing |
| `frontend` | node:20 build → nginx:alpine | 5173 | Serves the SPA; nginx reverse-proxies `/api/` to the backend with SSE tuning (`proxy_buffering off`, 600 s read timeout) |

The backend reaches host-installed Ollama via `host.docker.internal:11434`. Uploaded files live in a bind-mounted `./backend/uploads` volume; database state in a named `pgdata` volume. Healthchecks gate service readiness.

---

## 5. Technology Stack

### 5.1 Backend

| Component | Technology | Version | Role |
|---|---|---|---|
| Web framework | FastAPI | ≥ 0.115 | REST + SSE API, dependency injection, OpenAPI schema |
| Server | uvicorn[standard] | ≥ 0.30 | ASGI server (port 8100) |
| ORM / DB access | SQLAlchemy[asyncio] | ≥ 2.0.30 | Async ORM + core SQL for retrieval |
| Database driver | psycopg[binary] | ≥ 3.1 | Async Postgres driver (`postgresql+psycopg`) |
| Migrations | Alembic | ≥ 1.13 | Versioned schema migrations (2 revisions) |
| Vector search | pgvector (Python + extension) | ≥ 0.3 | `vector(768)` column, HNSW cosine index |
| Task queue | Celery | ≥ 5.4 | Optional document-processing worker (Redis broker, solo pool on Windows debug / prefork in prod) |
| Cache / limits | redis-py | ≥ 5.0 | Rate limiting, lockout, jti blacklist, broker |
| LLM client | openai (SDK) | ≥ 1.40 | OpenAI-compatible client pointed at Ollama by default |
| HTTP client | httpx | ≥ 0.27 | Health probes, outbound calls |
| PDF parsing | PyMuPDF | ≥ 1.26 | Per-page text extraction |
| OCR | pytesseract + Pillow | ≥ 0.3.10 / ≥ 10.4 | Scanned-page rasterization (200 DPI) and recognition |
| Auth | PyJWT + bcrypt | ≥ 2.8 / ≥ 4.1 | HS256 tokens, password hashing |
| Uploads | python-multipart | ≥ 0.0.9 | Multipart file handling |
| Settings | pydantic-settings | ≥ 2.3 | Typed, validated, `.env`-driven configuration |
| Language | Python | 3.12 | — |

### 5.2 Frontend

| Component | Technology | Version | Role |
|---|---|---|---|
| UI framework | React / react-dom | ^19.3.1 | SPA with three views (Documents, Chat, Analytics) |
| Language | TypeScript | ~7.0.2 | Type-safe frontend |
| Build tool | Vite | ^8.3.5 | Dev server (port 5173) + production build |
| Styling | Tailwind CSS | ^4.3.3 | CSS-first utility styling (slate/indigo palette) |
| Linter | oxlint | ^1.89.0 | React/TS linting (rules-of-hooks as error) |
| Routing / state | React hooks | — | Tab-based navigation; no router or global store by design |

### 5.3 AI models (default, local)

| Model | Task | Notes |
|---|---|---|
| Qwen 2.5 3B (`qwen2.5:3b`) | Answer generation | Temperature 0.2, max 1,024 tokens; strict "answer only from excerpts + cite `[n]`" system prompt |
| nomic-embed-text | Embeddings | 768 dimensions, validated against `EMBED_DIM` |

---

## 6. API & Integration Catalog

Base URL: `http://localhost:8100` · All responses JSON unless noted · Bearer-token auth (`Authorization: Bearer <access_token>`) · Interactive OpenAPI docs at `/docs`.

### 6.1 Health

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness + component probes (DB, LLM endpoint). Returns `status: ok\|degraded`, per-component state, model, edition, environment, latency. Never raises. |

### 6.2 Authentication (`/api/auth`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/register` | Create account (`201`). Password policy enforced (≥10 chars, ≥1 letter + 1 digit). Emails listed in `ADMIN_EMAILS` become admins. Returns access + refresh tokens and user profile; writes `auth.register` audit. |
| POST | `/api/auth/login` | Issue tokens. Lockout-gated (5 failures → 15 min). Generic error message; timing-equalized. |
| POST | `/api/auth/refresh` | Rotate refresh token (old one revoked; replaying a revoked token revokes the whole family). Returns fresh pair. |
| POST | `/api/auth/logout` | Revoke refresh token; blacklist access token `jti` until natural expiry. `204`. |
| GET | `/api/auth/me` | Current user profile (401 if anonymous). |

### 6.3 Documents (`/api/documents`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/documents` | Upload PDF (multipart `file`, optional `visibility=private\|public`). Magic-byte + size + page + quota validation. Returns `201` with status `processing`. Dispatches Celery task or in-process background job. |
| GET | `/api/documents` | List own + public documents (guests: public + guest space). |
| GET | `/api/documents/{id}` | Document detail incl. status, progress, page/chunk counts. Private docs of others → 404. |
| PATCH | `/api/documents/{id}/visibility` | Owner/admin only; writes audit. |
| POST | `/api/documents/{id}/retry` | Re-queue a failed/stuck document. |
| DELETE | `/api/documents/{id}` | Owner/admin only; chunks cascade; file removed; `204`. |

### 6.4 Search

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/search` | Hybrid retrieval without generation. Body: `{query ≤2000, top_k 1–20 (default 6), document_ids? ≤25}`. Returns per-result `chunk_id`, document, page, 280-char snippet, fused score, `matched_by[]`, plus `methods[]` used and `took_ms`. |

### 6.5 Chat & conversations (`/api`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/chat` | **Streaming (SSE)**. Body: `{question ≤4000, conversation_id?, top_k 1–12 (default 6), document_ids?}`. Events: `meta` (conversation id, retrieval ms, methods, cached), `citations`, `token` deltas, `done` (message id, token counts, latency), sanitized `error`. |
| GET | `/api/conversations` | List own conversations with message counts. |
| GET | `/api/conversations/{id}` | Ordered messages incl. citations, latency, feedback. |
| DELETE | `/api/conversations/{id}` | Delete conversation (owner/guest). `204`. |
| POST | `/api/messages/{id}/feedback` | 👍/👎 + comment on assistant messages. **Professional edition+.** `204`. |

### 6.6 Analytics

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/stats` | Scoped KPIs (admin = global, user = own, guest = guest): document/chunk counts, query counts, latency (avg total/retrieval/LLM), tokens + estimated cost, 15 recent queries, cache hit rate, feedback tallies, 14-day daily series (Professional/Enterprise). Zeroed payload on internal error (never 500s the dashboard). |

### 6.7 Administration (`/api/admin` — Enterprise edition, admin role)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/admin/users` | Last 500 users. |
| PATCH | `/api/admin/users/{id}` | Toggle active/role/display name (cannot disable self); audited. |
| GET | `/api/admin/documents` | All documents, optional status filter. |
| POST | `/api/admin/documents/{id}/reprocess` | Force re-ingestion. |
| GET | `/api/admin/audit` | Audit trail, optional action filter, limit 1–500. |
| GET | `/api/admin/overview` | Global counts: users (total/active), documents (total/failed), audit events. |

**Cross-cutting behavior:** rate limiting (429 + `Retry-After`), tenant isolation (404 for foreign private resources), edition gating (404 for higher-tier surfaces), error sanitization, request-ID propagation, and audit logging on all mutating security-relevant actions.

---

## 7. Data Model

Postgres 16 with the **pgvector** extension. Nine tables, managed by two Alembic migrations.

| Table | Purpose | Key fields |
|---|---|---|
| `users` | Accounts | UUID PK; unique indexed email; bcrypt `password_hash`; `role` (user/admin); `is_active`; timestamps |
| `documents` | Uploaded files | UUID PK; original + stored name/path; `status` (processing/ready/failed); `progress` (0–1); `error`; page/chunk counts; `visibility` (private/public, indexed); `owner_id` FK → users (SET NULL, indexed) |
| `chunks` | Searchable passages | UUID PK; `document_id` FK CASCADE (indexed); page; `chunk_index`; content; token estimate; embedding model; **`embedding vector(768)` (nullable)**; **generated `tsv tsvector` column (stored) with GIN index**; **HNSW index on embedding (`vector_cosine_ops`)** |
| `conversations` | Chat threads | UUID PK; title; `owner_id` FK CASCADE (indexed) |
| `messages` | Chat turns | UUID PK; conversation FK CASCADE (indexed); role; content; **citations JSON**; prompt/completion tokens; latency; feedback rating/comment |
| `query_logs` | Analytics source | created_at (indexed); owner (SET NULL, indexed); question; model; tokens; `cost_usd`; `cached` flag; retrieval/LLM/total ms; retrieved chunk IDs JSON |
| `refresh_tokens` | Session refresh | UUID PK; user FK CASCADE; unique indexed SHA-256 `token_hash`; **`family_id` (indexed)** for rotation/theft detection; expires_at; revoked |
| `audit_logs` | Security trail | actor id/email; indexed `action`; resource type/id; IP; user agent; JSON detail; created_at |
| `semantic_cache` | Answer cache | indexed `scope_hash` (SHA-256 of owner + doc set); question; **question embedding `vector(768)` with HNSW index**; answer; citations/methods JSON; model; tokens; hit count; indexed `expires_at` |

Design notes:

- **Every query is tenant-scoped in SQL**, not in application code — the security clause is part of the statement itself.
- Vector columns are nullable by design: chunks indexed without embeddings (embedding failure path) remain searchable via BM25.
- The full-text index is a **generated stored column**, so it can never drift from the content.

---

## 8. Configuration Reference

All configuration is environment-variable driven (pydantic-settings, `backend/.env`). Defaults shown; the guarded values are enforced at boot.

### 8.1 Application

| Variable | Default | Purpose |
|---|---|---|
| `APP_NAME` | DocuQuery | Display name |
| `EDITION` | `enterprise` | `basic` \| `professional` \| `enterprise` — feature gating |
| `ENVIRONMENT` | `local` | `local` \| `staging` \| `production` |
| `DEBUG` | `true` | Debug mode (must be `false` in production) |
| `CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | Allowed browser origins |
| `UPLOAD_DIR` | `backend/uploads` | PDF storage directory (auto-created) |
| `MAX_UPLOAD_MB` | `50` | Upload size cap (413 on exceed) |
| `MAX_PDF_PAGES` | `500` | Page cap |

### 8.2 AI providers (OpenAI-compatible)

| Variable | Default | Purpose |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | Generation endpoint (Ollama) |
| `LLM_API_KEY` | `ollama` | Bearer key for the provider |
| `LLM_MODEL` | `qwen2.5:3b` | Generation model |
| `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` | `0.2` / `1024` | Sampling controls |
| `LLM_COST_PROMPT_PER_1K` / `LLM_COST_COMPLETION_PER_1K` | `0.0` / `0.0` | Pricing for cost analytics |
| `EMBEDDINGS_BASE_URL` / `EMBEDDINGS_API_KEY` / `EMBEDDINGS_MODEL` | Ollama / `ollama` / `nomic-embed-text` | Embedding endpoint |
| `EMBED_DIM` | `768` | Expected embedding width (validated) |

### 8.3 Infrastructure

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://docuquery:docuquery@localhost:5433/docuquery` | Async Postgres DSN (converted to sync automatically for Celery) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis DSN |
| `USE_CELERY` | `false` | `true` → process documents via Celery worker; `false` → in-process background tasks |

### 8.4 Ingestion

| Variable | Default | Purpose |
|---|---|---|
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1000` / `150` | Chunking geometry (characters) |
| `OCR_DPI` / `OCR_MIN_CHARS_PER_PAGE` | `200` / `32` | OCR rasterization quality and trigger threshold |

### 8.5 Authentication & security

| Variable | Default | Purpose |
|---|---|---|
| `JWT_SECRET` | `change-me-in-production` | **Boot refused in production with the default** |
| `JWT_ALGORITHM` | `HS256` | Token signing |
| `JWT_EXPIRES_MINUTES` | `10080` (7 days) | Access-token lifetime |
| `REFRESH_TOKEN_EXPIRES_DAYS` | `30` | Refresh-token lifetime |
| `PASSWORD_MIN_LENGTH` | `10` | Password policy |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_SECONDS` | `5` / `900` | Lockout thresholds |
| `ADMIN_EMAILS` | `[]` | JSON list; registered emails here become admins |

### 8.6 Rate limits, quotas, cache, audit

| Variable | Default | Purpose |
|---|---|---|
| `RATE_LIMIT_AUTH` / `RATE_LIMIT_CHAT` / `RATE_LIMIT_UPLOAD` / `RATE_LIMIT_SEARCH` | `10` / `30` / `20` / `60` | Requests per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Sliding-window size |
| `MAX_DAILY_QUESTIONS` / `MAX_UPLOADS_PER_DAY` | `0` / `0` | Daily quotas (0 = unlimited; Professional tier) |
| `CACHE_ENABLED` / `CACHE_TTL_MINUTES` / `CACHE_SIMILARITY_THRESHOLD` | `true` / `60` / `0.92` | Semantic cache |
| `AUDIT_RETENTION_DAYS` | `0` | Audit retention (0 = forever) |

---

## 9. Deployment & Operations

### 9.1 Prerequisites

- Docker + Docker Compose (recommended path)
- [Ollama](https://ollama.com) installed on the host with two models pulled:
  ```bash
  ollama pull qwen2.5:3b
  ollama pull nomic-embed-text
  ```

### 9.2 One-command start

```bash
cd docuquery
docker compose up --build
```

| Surface | URL |
|---|---|
| Web UI | http://localhost:5173 |
| API | http://localhost:8100 |
| OpenAPI docs | http://localhost:8100/docs |
| Postgres | localhost:5433 (user/pass/db: `docuquery`) |
| Redis | localhost:6379 |

Migrations run automatically at backend startup (`alembic upgrade head`).

### 9.3 Local development (no Docker)

```bash
# Backend
cd docuquery/backend
python -m venv .venv && .venv/Scripts/activate   # (Windows Git Bash)
pip install -e ".[dev]"
alembic upgrade head
python run.py            # Windows-safe event-loop policy, serves 127.0.0.1:8100

# Frontend
cd docuquery/frontend
npm install
npm run dev              # http://localhost:5173 (proxies /api → :8100)
```

### 9.4 Demo data

```bash
python scripts/seed_corpus.py   # generates + ingests realistic enterprise PDFs
python scripts/run_eval.py 6    # retrieval evaluation against the golden set
```

### 9.5 Operational notes

- **Verification layers** (see [RUNBOOK.md](RUNBOOK.md)): automated tests → 35-check live demo (`scripts/live_demo.py`) → manual UI walkthrough.
- **Health monitoring**: `GET /api/health` reports DB and LLM component status; the UI health pill polls it every 30 s.
- **Edition delivery check**: `scripts/edition_check.py` probes a running server to confirm which edition is actually served.
- **Windows**: use `python run.py` (it installs the selector event-loop policy required by the async Postgres driver).

---

## 10. Quality Assurance & Benchmarks

### 10.1 Automated test suite — 108 tests, all green

| Layer | Count | Scope |
|---|---|---|
| Unit (no DB, < 2 s) | 56 | API schemas, auth logic, chunker, PDF parsing, rate limiter, retrieval fusion, security helpers, token lifecycle |
| Integration (real Postgres + fake AI providers) | 52 | Auth lifecycle, chat pipeline, ingestion edge cases, tenant isolation (dedicated regression), admin & hardening |

The integration rig creates a disposable `docuquery_test` database and substitutes deterministic fake AI providers, so the full stack is exercised without network or model costs. Run everything with:

```bash
python scripts/run_tests.py          # all | unit | integration
```

### 10.2 Retrieval quality — golden evaluation

A **20-question golden dataset** spans 7 sample documents and 7 categories (architecture, conceptual, ethics, finance, policy, security, technical). A hit requires the expected document *and* page (±1).

| Metric (Top-K) | Result |
|---|---|
| Hit rate @ 1 | **85%** |
| Hit rate @ 3 | **95%** |
| Hit rate @ 6 | **100%** |
| Average retrieval latency | **8.4 ms** |

```bash
python scripts/run_eval.py 6    # exits non-zero if hit rate is 0
```

### 10.3 Live end-to-end verification

`scripts/live_demo.py` runs **35 checks against a running server** over real HTTP, real database, real embeddings, and real LLM — generating its own PDFs, parsing SSE streams, and exercising auth, quotas, rate limits, admin, and audit end to end.

### 10.4 Quality gates (definition of done)

Per [TESTING_STRATEGY.md](TESTING_STRATEGY.md): every feature lands with unit + integration coverage, the eval harness must not regress, and the live demo must pass before a release is called production-ready.

---

## 11. Security & Privacy Model

Full detail in [SECURITY_MODEL.md](SECURITY_MODEL.md). The five design principles:

1. **Deny by default** — every resource requires an explicit allow check; mutations by guests are rejected; nothing is public unless marked public.
2. **Isolation by construction** — tenant scope is part of every SQL statement, not an application-layer filter; a missing check cannot leak data.
3. **Fail honest** — unauthorized access returns 404 (existence never leaked); stream errors are sanitized; unhandled exceptions become generic 500s with a request ID; the server refuses to boot into an insecure production configuration.
4. **Audit trail** — security-relevant actions are recorded with actor, IP, user agent, and detail JSON, with configurable retention.
5. **Standard mechanisms** — bcrypt, JWT (HS256), Redis-backed sliding windows; no home-rolled cryptography.

**Privacy posture:** documents, chunks, embeddings, query logs, and model inference are entirely local by default. The only outbound calls the stack makes are to the endpoints *you* configure (LLM/embeddings), and the default configuration points them at localhost Ollama.

### Control summary

| Control | Status |
|---|---|
| Password hashing (bcrypt) + policy | ✅ implemented |
| JWT access tokens + jti blacklist on logout | ✅ implemented |
| Rotating refresh tokens + family theft detection | ✅ implemented |
| Login lockout + rate limiting (Redis, sliding window) | ✅ implemented |
| Tenant isolation (SQL-level, deny-by-default) | ✅ implemented |
| Audit trail + admin console (Enterprise) | ✅ implemented |
| Security headers, error sanitization, request IDs | ✅ implemented |
| Production boot guards (secret/debug) | ✅ implemented |
| Workspaces & fine-grained RBAC | 🗺 planned (see roadmap) |
| SSO / OIDC integration | 🗺 planned |

---

## 12. Current Limitations

Stated plainly, for evaluation honesty:

1. **PDF-only ingestion** — DOCX/PPTX/XLSX and image formats are not yet accepted (roadmap item, §13).
2. **No reranker** — ranking is RRF fusion only; a cross-encoder reranker is a planned accuracy upgrade.
3. **Answer-quality metric is lightweight** — the eval harness computes retrieval hit-rate precisely, but answer faithfulness uses a token-overlap F1 rather than an LLM-judge (the scoring function is in place, the judge integration is not).
4. **No frontend test suite / CI pipeline** — quality is enforced by 108 backend tests and the live-demo script; frontend E2E tests and a CI workflow (GitHub Actions) are roadmap items.
5. **No URL routing** — navigation is tab state; deep links (e.g., `/chat/abc123`) are not addressable.
6. **Auth token in localStorage** — standard for SPAs but vulnerable to XSS by design trade-off; HttpOnly-cookie sessions are a hardening option.
7. **Single active model config** — one generation model and one embedding model per deployment; per-query model routing is future scope.
8. **OCR is text-only** — tables and handwriting inside scans are not structurally understood.
9. **Guest space is shared** — all anonymous users share one guest document space (by design for demos; disable by requiring auth).

---

## 13. Future Scope & Roadmap

Organized by horizon; each item lists the business value it unlocks. Sequencing and phase detail live in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md); pricing impact in [COMMERCIALIZATION.md](COMMERCIALIZATION.md).

### 13.1 Near-term (next release train)

| Item | What it is | Business value |
|---|---|---|
| **Office format support** | Ingest DOCX, PPTX, XLSX (and EML) alongside PDF | Covers the majority of real enterprise corpora; removes the #1 onboarding blocker |
| **Cross-encoder reranker** | Re-score fused candidates with a local reranking model (e.g., bge-reranker) before returning top-k | Higher answer precision on ambiguous questions; strengthens the 100% Top-6 story toward Top-1 |
| **Citation-highlighting PDF viewer** | Click a citation → the source PDF opens scrolled to the page with the passage highlighted | Closes the loop from answer → evidence; major trust and auditability win |
| **Frontend E2E tests + CI pipeline** | Playwright smoke suite; GitHub Actions running unit → integration → eval-gate on every PR | Regression-proof delivery; CI eval gate blocks quality regressions automatically |

### 13.2 Mid-term

| Item | What it is | Business value |
|---|---|---|
| **Workspaces & RBAC** | Team/group containers with role-based access (owner/editor/viewer) per workspace | Moves the product from personal tool to departmental platform; prerequisite for larger deals |
| **SSO / OIDC** | Enterprise identity-provider login (Azure AD, Okta) | Enterprise procurement requirement; removes password management |
| **Feedback-driven analytics** | Aggregate 👍/👎 signals per document/question category; low-confidence question surfacing | Turns user feedback into a corpus-quality loop; identifies content gaps |
| **LLM-judge answer evaluation** | Replace token-overlap F1 with an LLM-judged faithfulness metric in the eval harness | Marketing-grade quality claims with reproducible numbers |
| **Advanced OCR** | Table structure recognition and handwriting support in scanned documents | Unlocks finance/logistics document types (invoices, forms) fully |
| **Multi-model routing** | Per-question model selection (fast model for simple, strong model for complex) with cost tracking | Latency/cost optimization; supports provider diversity per customer |

### 13.3 Long-term

| Item | What it is | Business value |
|---|---|---|
| **Cloud connectors** | Scheduled sync from Google Drive, SharePoint, and S3 buckets with change detection | "Zero-touch" adoption: documents flow in without manual upload; expands TAM |
| **White-label packaging** | Branded, edition-licensed deployments with customer logos/domains | Direct enablement of the three-edition commercial model |
| **Horizontal worker scaling** | Multiple Celery workers with per-tenant fairness and backpressure | Large-corpus throughput for enterprise volumes |
| **Conversation memory & follow-ups** | Long-lived conversational context beyond the current 6-message window with explicit memory controls | More natural multi-turn research workflows |
| **Structured extraction API** | Schema-guided field extraction (invoice totals, clause lists) on top of retrieval | New product surface: from Q&A to automated data entry |

### 13.4 Explicitly out of scope (by design)

- **Cloud-only SaaS**: the local-first privacy guarantee is the product; a hosted variant would be a separate, opt-in offering.
- **Training/fine-tuning customer models**: grounding (not customization) is the chosen accuracy strategy.

---

## Appendix A — Utility Scripts

All under `docuquery/scripts/`:

| Script | Purpose |
|---|---|
| `run_tests.py` | Two-phase runner: unit tests (no DB) → integration tests against a disposable `docuquery_test` database with fake AI providers. Args: `all` \| `unit` \| `integration`. Skips integration cleanly when Postgres is unavailable. |
| `run_eval.py` | Retrieval evaluation against the 20-question golden set. Arg: `top_k` (default 6). Non-zero exit on zero hit rate. |
| `live_demo.py` | 35-check end-to-end verification against a **running** server (real HTTP/DB/embeddings/LLM). Requires `ADMIN_EMAILS` to include `admin@demo.local`. |
| `edition_check.py` | Unauthenticated probe that detects which edition a running server actually serves vs. its configured edition. |
| `seed_corpus.py` | Generates realistic enterprise PDFs (handbook, invoices, …) into `backend/sample_data` and ingests them for instant demo readiness. |

## Appendix B — Sample Corpus

`docuquery/backend/sample_data/` ships three demonstration PDFs chosen to exercise the full pipeline:

| File | Exercises |
|---|---|
| `employee_handbook_2026.pdf` | Policy Q&A; matches the golden dataset |
| `acme_invoice_scanned.pdf` | **Scanned-document OCR path** (image-only pages) |
| `On_the_Creativity_of_AI_Agents-1.pdf` | Long technical/research text; multi-topic retrieval |

## Appendix C — Glossary

| Term | Meaning |
|---|---|
| **RAG** | Retrieval-Augmented Generation — answering from retrieved document excerpts instead of model memory |
| **RRF** | Reciprocal Rank Fusion — merging ranked lists from multiple retrievers with `score = Σ 1/(k + rank)`, here k = 60 |
| **HNSW** | Hierarchical Navigable Small World — approximate-nearest-neighbor index for fast vector similarity search |
| **BM25 / FTS** | Lexical ranking over inverted-indexed terms; here Postgres full-text search with `ts_rank_cd` |
| **pgvector** | Postgres extension adding a `vector` column type and similarity operators/indexes |
| **OCR** | Optical Character Recognition — recovering text from scanned page images (Tesseract, 200 DPI here) |
| **SSE** | Server-Sent Events — one-way HTTP streaming used for token-by-token chat delivery |
| **HNSW/Tsv/GIN** | Index types: vector ANN (HNSW), full-text vector column (tsvector), inverted text index (GIN) |
| **jti** | JWT token ID — used to blacklist individual access tokens on logout |
| **Tenant isolation** | Guarantee that a user can only ever retrieve/mutate documents they own or that are public |
| **Semantic cache** | Answer cache keyed by question *meaning* (embedding cosine similarity), not exact text |
| **Edition gating** | Runtime feature gating by the `EDITION` env var; higher-tier surfaces 404 for lower editions |

---

*DocuQuery — your documents, your answers, your hardware. © 2026 · MIT License.*
