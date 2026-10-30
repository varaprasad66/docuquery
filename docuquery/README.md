# DocuQuery — Production-Grade Document Intelligence (RAG + OCR)

A fully local, privacy-first document intelligence platform built for enterprise knowledge retrieval over complex digital and scanned documents. Features hybrid retrieval (Postgres full-text + pgvector semantic search) fused with Reciprocal Rank Fusion (RRF), real-time token streaming with source-level citations, and an automated background ingestion pipeline with OCR fallback.

Ingests **PDF, DOCX, PPTX, XLSX, HTML, TXT and MD** files (validated by magic bytes and zip structure, with zip-bomb protection) plus **URL imports** with SSRF protection. Citations point at the exact source location — page, slide, sheet row range, or section heading.

Built and structured as a production showcase system.

## Editions

One codebase, three sellable editions selected via `EDITION` in the backend `.env`:

| Edition | Adds beyond the previous tier |
|---|---|
| `basic` (Starter) | Core RAG: PDF/OCR ingestion, hybrid chat + citations, conversations, accounts, tenant isolation |
| `professional` (Professional) | Usage & cost analytics + daily series, semantic answer cache (cost optimization), quotas, answer feedback, search filters |
| `enterprise` (Enterprise) | Admin console API, audit trail, full auth lifecycle (refresh rotation, revocation, lockout), 108-test regression rig |

Security fundamentals (tenant isolation, rate limiting, sanitized errors, security
headers) are enabled in **every** edition.

---

## Documentation

| Document | Audience | Purpose |
|---|---|---|
| [Product Overview & Editions](docs/PRODUCT_OVERVIEW.md) | Clients | One-pager: features, three editions, differentiators, use cases |
| [Architecture & RAG Workflow](docs/ARCHITECTURE.md) | Clients | End-to-end system walkthrough with diagrams (no code) |
| [Security Model](docs/SECURITY_MODEL.md) | Clients | Auth, tenant isolation, data protection, abuse resistance |
| [Testing & Quality Strategy](docs/TESTING_STRATEGY.md) | Clients | 108-test rig, quality gates, benchmark methodology |
| [Implementation Plan](docs/IMPLEMENTATION_PLAN.md) | Internal | Phased roadmap: features, edge cases, security, tests per phase |
| [Commercialization](docs/COMMERCIALIZATION.md) | Internal | How to package, price, and deliver the three editions |
| [Docs Index](docs/README.md) | Both | Full documentation set |

---

## Architecture

```mermaid
flowchart TD
    subgraph Client ["Frontend (React 19 + Vite + Tailwind)"]
        UI_DOC["Documents View\n(Dropzone + Live Status)"]
        UI_CHAT["Chat View\n(SSE Streaming + Citations)"]
        UI_STATS["Analytics & Stats\n(Latency + Token Tracking)"]
    end

    subgraph API ["Backend API (FastAPI)"]
        AUTH["JWT Auth & Multi-tenant Scoping"]
        RATE["Sliding-Window Rate Limiter"]
        SSE["SSE Event Streamer"]
        ROUTER["REST Endpoints\n(/documents, /chat, /search, /stats)"]
    end

    subgraph Ingestion ["Ingestion Pipeline (Celery / Background)"]
        PARSE["Parser Registry\n(PDF+OCR, DOCX, PPTX, XLSX, HTML, TXT, MD)"]
        OCR["Tesseract OCR Fallback\n(Scanned Pages / Low Density)"]
        CHUNK["Heading & Paragraph Chunker\n(1000 chars, 150 overlap)"]
        EMBED["Ollama nomic-embed-text\n(768-dim Vectors)"]
    end

    subgraph Storage ["Enterprise Persistence"]
        PG_DOCS["PostgreSQL 16\n(Documents, Chunks, Users)"]
        PG_VEC["pgvector Extension\n(HNSW Cosine Index)"]
        PG_TSV["Postgres Full-Text\n(GIN Index on tsvector)"]
        REDIS["Redis 7\n(Celery Broker & Event Queue)"]
    end

    UI_DOC -->|File Upload / URL Import| ROUTER
    ROUTER -->|Enqueue Task| REDIS
    REDIS --> Ingestion
    PARSE -->|Text Layer Found| CHUNK
    PARSE -->|Scanned / Image Page| OCR
    OCR --> CHUNK
    CHUNK --> EMBED
    EMBED --> PG_DOCS
    EMBED --> PG_VEC
    CHUNK --> PG_TSV

    UI_CHAT -->|Question Stream| SSE
    SSE -->|Hybrid Search| PG_VEC
    SSE -->|Hybrid Search| PG_TSV
    PG_VEC & PG_TSV -->|Reciprocal Rank Fusion (RRF)| SSE
    SSE -->|Cited Streaming Context| UI_CHAT
```

---

## Key Technical Differentiators

| Capability | DocuQuery Implementation | Production Value |
|---|---|---|
| **Hybrid Retrieval** | Postgres `ts_rank_cd` full-text search + `pgvector` HNSW cosine similarity fused via **Reciprocal Rank Fusion (RRF)** | Outperforms pure semantic search on domain jargon, part numbers, and invoices; works even if embeddings are offline. |
| **OCR Fallback** | Dynamic per-page inspection (text density threshold); scanned pages automatically rasterize to Tesseract OCR at 200 DPI | Handles mixed digital/scanned PDFs without user intervention or manual flags. |
| **Multi-Format Ingestion** | Parser registry behind one pipeline: PDF (PyMuPDF + OCR), DOCX (headings + tables), PPTX (slides), XLSX (row groups with repeated headers), HTML (sections + tables), TXT/MD (heading sections); content validated by magic bytes/zip structure with zip-bomb and SSRF-guarded URL import | One ingestion path for the document types enterprises actually have; citations still point at the exact source location. |
| **Resilient Ingestion** | Batch processing with live progress callbacks; Celery broker with transparent fallback to in-process background tasks | Ingests multi-page PDFs (e.g. 24+ page academic papers) smoothly without UI freezing or loop hang. |
| **Page-Level Citations** | Every answer embeds `[1]`, `[2]` markers linked to source cards displaying document name, source location (page / slide / sheet row range / section heading), and text excerpt | Eliminates hallucinations; allows users to verify answers directly against the original source. |
| **Token & Cost Tracking** | Every query logs prompt/completion tokens, retrieval latency, and LLM latency into `QueryLog` with an analytics dashboard | Real-time visibility into operational spend and retrieval performance. |
| **Multi-Tenant Ready** | JWT authentication with per-document and per-conversation ownership scoping | Supports authenticated multi-tenant separation with a clean guest demo mode. |
| **Automated Eval Harness** | Golden dataset (20+ Q&A pairs) testing Recall@k hit rate and answer faithfulness | Automated benchmark regression testing for enterprise reliability. |

---

## Tech Stack

- **LLM**: Qwen 2.5 3B via Ollama (OpenAI-compatible API; swappable to OpenAI/Anthropic/Z.ai via `.env`)
- **Embeddings**: `nomic-embed-text` (768 dimensions) via Ollama
- **Vector Database**: PostgreSQL 16 + `pgvector` with HNSW cosine indexing and GIN full-text indexing
- **Backend Service**: FastAPI, SQLAlchemy 2 (async + sync), Alembic migrations, Celery, Redis
- **Document Processing**: PyMuPDF (`fitz`), Pillow, Tesseract OCR (`pytesseract`), python-docx, python-pptx, openpyxl, BeautifulSoup4
- **Frontend SPA**: React 19, TypeScript, Vite, Tailwind CSS v4
- **Testing**: Python test suite — 84 unit tests + 59 integration tests against a real disposable PostgreSQL database, including a dedicated tenant-isolation regression suite

---

## Quickstart

### Option A: One-Command Docker Deployment

Ensure Docker Desktop is running, then run:

```bash
docker compose up --build
```

- **Frontend UI**: [http://localhost:5173](http://localhost:5173)
- **FastAPI OpenAPI Docs**: [http://localhost:8100/docs](http://localhost:8100/docs)
- **PostgreSQL**: `localhost:5433` (user: `docuquery`, db: `docuquery`)
- **Redis**: `localhost:6379`

### Option B: Local Development Setup

#### 1. Start Infrastructure Containers
```bash
docker compose up db redis -d
```

#### 2. Backend Setup
```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

pip install -e .
alembic upgrade head
python run.py
```

*Note: The backend starts at `http://127.0.0.1:8100`.*

#### 3. Frontend Setup
```bash
cd ../frontend
npm install
npm run dev
```

*The Vite dev server starts at `http://localhost:5173` with automatic API proxying.*

---

## Evaluation Benchmark

DocuQuery includes an automated evaluation harness testing retrieval accuracy and faithfulness against a golden ground-truth dataset spanning contracts, technical papers, and invoices.

To execute the eval harness:

```bash
python scripts/run_eval.py 6
```

### Golden Set Results (Top-6 RRF Hybrid Search)

```
=======================================================
DocuQuery RAG Evaluation (20 Golden Q&A Pairs)
=======================================================
[HIT] eval-01 | Top-1: Y | Top-3: Y | Top-6: Y | 12ms | Primary thesis on AI agents & human creativity...
[HIT] eval-02 | Top-1: Y | Top-3: Y | Top-6: Y | 8ms  | Novelty & value metrics for agent artifacts...
[HIT] eval-08 | Top-1: Y | Top-3: Y | Top-6: Y | 6ms  | Annual PTO accrual & rollover policy...
[HIT] eval-13 | Top-1: Y | Top-3: Y | Top-6: Y | 9ms  | Total amount due on invoice #INV-2026-089...
...
-------------------------------------------------------
EVALUATION SUMMARY
-------------------------------------------------------
Total Test Cases:    20
Hit Rate @ Top-1:    85.0%
Hit Rate @ Top-3:    95.0%
Hit Rate @ Top-6:    100.0%
Avg Retrieval Time:  8.4 ms
-------------------------------------------------------
```

---

## Running Automated Tests

Two-phase runner (unit tests + integration tests against a disposable `docuquery_test` database):

```bash
python scripts/run_tests.py          # both phases
python scripts/run_tests.py unit     # unit only (no database needed)
```

Verified test coverage includes:
- **Multi-format ingestion** — DOCX/PPTX/XLSX/HTML/TXT/MD parsing with location citations (slide/sheet rows/section), magic-byte and zip-structure validation, extension masquerading rejection, zip-bomb guards (entry count + uncompressed size), and SSRF-protected URL import (private/loopback/link-local targets, embedded credentials, redirect re-validation, content-type allow-list, size cap).
- **Tenant isolation regression suite** — user A's private documents can never surface in user B's or a guest's search results, chat citations, document listings, stats, or conversations (14 dedicated tests).
- **Access-control matrix** — guest/user/owner/admin × read/mutate across all document visibilities.
- **Auth lifecycle** — refresh-token rotation with replay detection (family revocation), logout revocation, account lockout after 5 failures, password policy, disabled accounts.
- **Rate limiting & lockout** — sliding-window correctness, per-identity isolation, `Retry-After`, bounded memory.
- **Ingestion edge cases** — encrypted PDFs, PDF forgery, empty files, embedding-provider outage → automatic BM25-only degraded mode, delete cascades.
- **Chat pipeline** — SSE streaming with citations, semantic cache hits (zero-cost repeats) with per-user scoping and corpus-change invalidation, daily quotas, feedback loop, sanitized streaming errors.
- **Analytics scoping** — per-user / guest / global stats, cost fields, daily series.
- **Admin & audit** — admin-only surfaces (404-shaped for non-admins), user management, audit trail.
- **Chunker, fusion, tokens, costs, editions** — unit level.

---

## License

MIT License. Designed and developed as a client-ready document intelligence showcase.
