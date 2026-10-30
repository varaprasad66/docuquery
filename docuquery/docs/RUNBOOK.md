# DocuQuery — Runbook: Run, Check & Test Everything

> **Audience:** internal / engineering + during client demos.
> Three layers of verification, from fast to most realistic:
> 1. **Automated tests** (108, no server needed) → 2. **Live end-to-end verification** (35 checks, real server) → 3. **Manual UI walkthrough**.

---

## 1. Prerequisites & startup

| Requirement | Check |
|---|---|
| Docker Desktop running with `docuquery-db` + `docuquery-redis` up | `docker ps` |
| Ollama running with `qwen2.5:3b` + `nomic-embed-text` pulled | `curl http://localhost:11434/api/tags` |
| Backend venv at `backend/.venv` | `cd backend && .venv/Scripts/python.exe --version` |

```bash
# 1. Infrastructure (if not already up)
cd docuquery
docker compose up db redis -d

# 2. Backend (apply migrations if needed)  → http://127.0.0.1:8100  (API docs at /docs)
cd backend
.venv/Scripts/python.exe -m alembic upgrade head     # only when migrations changed
.venv/Scripts/python.exe run.py                      # sets the Windows event-loop policy — always use this, not uvicorn directly

# 3. Frontend  → http://localhost:5173
cd ../frontend
npm install && npm run dev
```

**Important:** the backend reads `.env` once at startup — after editing `.env`
(`EDITION`, `ADMIN_EMAILS`, quotas, model pricing…), **restart the backend**.

`.env` currently ships with `EDITION=enterprise` and `ADMIN_EMAILS=["admin@demo.local"]`
— registering that email creates the admin account (done automatically by the demo script).

---

## 2. Automated test suite (fast, CI-grade)

```bash
cd backend
.venv/Scripts/python.exe ../scripts/run_tests.py            # both phases
.venv/Scripts/python.exe ../scripts/run_tests.py unit       # 56 unit tests, <2 s, no DB
```

- **Phase 1 — 56 unit tests:** access-control matrix, RRF fusion, token lifecycle &
  revocation, rate limiter & lockout, costs, editions, chunker, schemas.
- **Phase 2 — 52 integration tests:** real disposable PostgreSQL (`docuquery_test`,
  auto-created), deterministic fake AI, fresh process. Includes the **tenant-isolation
  regression suite** (14 tests), auth lifecycle, ingestion edge cases (encrypted/forged
  PDFs, provider outage → BM25 degradation), chat pipeline (cache, quotas, feedback,
  sanitized errors), analytics scoping, admin & audit, rate limiting, security headers.

Exit code 0 = all green. Integration tests skip cleanly if Postgres is unreachable and
never touch the dev database.

## 3. Live end-to-end verification (real server, real AI)

With the backend **running**:

```bash
cd backend
.venv/Scripts/python.exe ../scripts/live_demo.py
```

35 checks across the tiers, over real HTTP with real embeddings + LLM:

| Section | Proves |
|---|---|
| Health | DB + LLM components, security headers, all routers registered |
| Starter | admin bootstrap, weak-password rejection, private upload → ingest → hybrid search (`vector`+`bm25`), streamed cited chat, guest/other-user denial (401/404), conversation persistence |
| Professional | **cache hit on repeat question (zero prompt tokens)**, cache tenant-scoping, feedback + ownership, search-by-document filter, per-user analytics with 14-day series and cost fields |
| Enterprise | admin user disable → token dies, non-admin gets 404, audit trail actions, global overview, owner-controlled visibility change, refresh rotation + replay detection, **login lockout (429)**, delete cascade removes content from search |

**Notes:**
- Wait ~60 s between consecutive runs — the auth rate limiter (10/min per IP) will
  otherwise block the second run. That's the product working, not a bug.
- The script cleans up after itself (deletes its demo document). Demo users remain in
  the dev DB — harmless, or clean with SQL.
- Exit code 0 and `RESULT: 35/35 checks passed` = everything works.

## 4. Manual verification (UI walkthrough)

1. **Starter:** open http://localhost:5173 → register a user → upload
   `backend/sample_data/employee_handbook_2026.pdf` → watch progress → ask
   "How many PTO days do I get per year?" → answer streams with `[1]` citation → click
   the source card (document + page). Upload `acme_invoice_scanned.pdf` to see the
   **OCR path** on a scanned document. Ask "What is the total amount due on invoice
   INV-2026-089?" — hybrid retrieval shines on exact codes.
2. **Professional:** ask the same question twice (second answer is instant — cache) →
   check http://localhost:5173 stats page for latency/tokens/cost + daily series →
   rate an answer 👍.
3. **Enterprise:** log in as `admin@demo.local` → hit the admin API via the OpenAPI UI
   at http://127.0.0.1:8100/docs (`/api/admin/users`, `/api/admin/audit`,
   `/api/admin/overview`) → disable a user and watch their token die on next request.
4. **Tenant isolation, by hand:** in a private/incognito window (guest) upload a
   document — it appears for guests, never for your logged-in account.

Interactive API testing: **http://127.0.0.1:8100/docs** — every endpoint (22 paths)
with schemas; use the `Authorize` button to paste a Bearer token.

## 5. The three editions — how to check each part

The three product tiers (Starter / Professional / Enterprise) are **one codebase gated
by the `EDITION` setting** — there are no separate folders or UIs. You check a tier by
starting the server with that edition and running the tier checker:

```bash
cd backend

# --- check the CURRENTLY running server ---
.venv/Scripts/python.exe ../scripts/edition_check.py

# --- flip to another tier and check it ---
EDITION=basic          .venv/Scripts/python.exe run.py    # terminal 1
.venv/Scripts/python.exe ../scripts/edition_check.py      # terminal 2
# then restart with EDITION=professional, then EDITION=enterprise (or edit .env)
```

The checker probes the feature surfaces from the outside (no accounts, no data) and
prints the gating matrix:

```
================================================================
Configured edition : basic (Starter)
Detected edition   : basic (Starter)
================================================================
  ✓ Core RAG (upload/chat/search)          open
  ✓ Professional: cache/quotas/feedback    hidden
  ✓ Enterprise: admin console & audit      hidden
================================================================
✓ MATCH: server is serving the Starter edition.
```

| Edition | Core RAG | Professional surfaces | Enterprise surfaces | Header badge in UI |
|---|---|---|---|---|
| `basic` (Starter) | ✅ | hidden (404) | hidden (404) | STARTER |
| `professional` | ✅ | ✅ cache/quotas/feedback/filters | hidden (404) | PROFESSIONAL |
| `enterprise` | ✅ | ✅ | ✅ admin console & audit (auth-gated) | ENTERPRISE |

**What "hidden" means:** a lower-tier client gets 404 — the surface is never advertised.

**Visual check:** the UI header (localhost:5173) shows a badge — STARTER / PROFESSIONAL /
ENTERPRISE — read live from `/api/health`. Flip `EDITION`, restart the backend, refresh
the page: the badge changes. Client-facing details of what each tier includes:
[PRODUCT_OVERVIEW.md](PRODUCT_OVERVIEW.md); the sales/delivery strategy:
[COMMERCIALIZATION.md](COMMERCIALIZATION.md) (internal).

## 6. Troubleshooting

| Symptom | Cause & fix |
|---|---|
| `/api/health` says `database: down` | Backend started without the selector event loop — start via `python run.py`, never bare `uvicorn` on Windows |
| `status: degraded` | DB or Ollama unreachable — check `docker ps` and `curl localhost:11434/api/tags` |
| Chat answers "temporarily unavailable" | LLM model missing → `ollama pull qwen2.5:3b` |
| 429 on register/login during testing | Auth rate limit (10/min) or lockout (5 failures) engaged — wait 60 s / 15 min, or restart backend (resets in-process fallback only) |
| Dim-mismatch error on ingest | `EMBED_DIM` must match the embedding model (nomic-embed-text = 768) |
| Admin endpoints 404 | `EDITION` below enterprise, or user lacks the admin role (`ADMIN_EMAILS` bootstrap requires backend restart before registering) |
| Tests fail to connect | Start the DB: `docker compose up db redis -d`; integration tests auto-create `docuquery_test` |
