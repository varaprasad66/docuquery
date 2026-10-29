# DocuQuery — Production-Grade Document Intelligence (RAG + OCR)

A fully local, privacy-first document intelligence platform built for enterprise knowledge retrieval over complex digital and scanned documents. Features hybrid retrieval (Postgres full-text + pgvector semantic search) fused with Reciprocal Rank Fusion (RRF), real-time token streaming with page-level citations, and an automated background ingestion pipeline with OCR fallback.

Built and structured as a production showcase system.

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
        PARSE["PyMuPDF Digital Text Layer"]
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

    UI_DOC -->|PDF Upload| ROUTER
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
| **Resilient Ingestion** | Batch processing with live progress callbacks; Celery broker with transparent fallback to in-process background tasks | Ingests multi-page PDFs (e.g. 24+ page academic papers) smoothly without UI freezing or loop hang. |
| **Page-Level Citations** | Every answer embeds `[1]`, `[2]` markers linked to source cards displaying document name, page number, and text excerpt | Eliminates hallucinations; allows users to verify answers directly against original source pages. |
| **Token & Cost Tracking** | Every query logs prompt/completion tokens, retrieval latency, and LLM latency into `QueryLog` with an analytics dashboard | Real-time visibility into operational spend and retrieval performance. |
| **Multi-Tenant Ready** | JWT authentication with per-document and per-conversation ownership scoping | Supports authenticated multi-tenant separation with a clean guest demo mode. |
| **Automated Eval Harness** | Golden dataset (20+ Q&A pairs) testing Recall@k hit rate and answer faithfulness | Automated benchmark regression testing for enterprise reliability. |

---

## Tech Stack

- **LLM**: Qwen 2.5 3B via Ollama (OpenAI-compatible API; swappable to OpenAI/Anthropic/Z.ai via `.env`)
- **Embeddings**: `nomic-embed-text` (768 dimensions) via Ollama
- **Vector Database**: PostgreSQL 16 + `pgvector` with HNSW cosine indexing and GIN full-text indexing
- **Backend Service**: FastAPI, SQLAlchemy 2 (async + sync), Alembic migrations, Celery, Redis
- **Document Processing**: PyMuPDF (`fitz`), Pillow, Tesseract OCR (`pytesseract`)
- **Frontend SPA**: React 19, TypeScript, Vite, Tailwind CSS v4
- **Testing**: Python `unittest` suite (17 core unit/integration tests)

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

Run the complete backend test suite:

```bash
python scripts/run_tests.py
```

Verified test coverage includes:
- Chunker boundary splitting, sliding window overlap, and pathological long-string splits.
- 24-page PDF text extraction and progress callback verification.
- Password hashing with Bcrypt and JWT token lifecycle.
- Vector literal formatting and reciprocal rank fusion snippet math.
- API route contracts, input validation, and unauthorized guards.

---

## License

MIT License. Designed and developed as a client-ready document intelligence showcase.
