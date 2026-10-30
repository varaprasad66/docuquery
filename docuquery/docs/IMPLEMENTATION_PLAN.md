# DocuQuery — Phased Implementation Plan (Production Grade)

> **Audience:** internal / engineering.
> **Status legend:** ✅ shipped · 🔶 partial (works, needs hardening) · ⬜ planned.
> Each phase lists: objectives, task breakdown, **edge cases** (explicitly enumerated —
> the "highest order" cases), **security controls**, **testing additions**, and
> **acceptance criteria**. A phase is only complete when every acceptance criterion is
> demonstrably met, including its tests.

---

## Progress log

**v0.3 (current) — Phases 0–2 delivered for the Enterprise edition.** The product is
now packaged into three sellable editions (`basic` / `professional` / `enterprise`,
see `app/editions.py` and [PRODUCT_OVERVIEW.md](PRODUCT_OVERVIEW.md)). Shipped:

- Edition gating with feature surfaces (admin/audit = enterprise; cache/quotas/feedback/
  analytics series = professional; security fundamentals everywhere).
- **Tenant isolation end-to-end:** visibility column, scoped retrieval in both search
  legs, deny-by-default access helper, guest space, per-user stats, scoped conversations.
- **Auth lifecycle:** refresh-token rotation with family-revocation theft detection,
  logout revocation (jti blacklist), login lockout, password policy, disabled accounts.
- **Abuse resistance:** Redis-backed sliding-window rate limiting (in-process fallback,
  bounded memory) on chat/upload/search/auth, daily quotas, upload hardening.
- **Analytics & cost optimization:** per-model cost calculation, per-user/global stats
  scoping, 14-day daily usage series, semantic answer cache with automatic corpus
  invalidation, cache hit-rate metrics, feedback loop (👍/👎 + comments).
- **Enterprise surfaces:** admin console API (users, global overview, audit trail,
  document triage/reprocess), audit logging on all sensitive actions.
- **Platform hardening:** security headers, request-ID correlation, sanitized errors
  (including streaming), encrypted/forged PDF rejection.
- **Testing rig:** 56 unit + 52 integration tests (real disposable Postgres, fake AI
  providers, isolated rate-limit backends) — including the 14-test tenant-isolation
  regression suite. All 108 green.

Remaining phases below are unchanged and are the basis for client-specific quotes
(Phase 3 formats, Phase 4 reranker, Phase 5 viewer/workspaces, Phase 6 ops, Phase 7
connectors).

---

## Current-state audit (updated at v0.3)

| Area | Status | Notes |
|---|---|---|
| Ingestion (PDF → OCR → chunk → embed → pgvector) | ✅ | PyMuPDF + Tesseract fallback, batched embeddings, progress tracking, retry, encrypted/forged rejection |
| Hybrid retrieval + RRF | ✅ | pgvector HNSW + Postgres FTS, fusion extracted + unit-tested, BM25-only degraded mode |
| **Tenant-scoped retrieval** | ✅ | Both legs owner-scoped; regression suite enforces it |
| Streaming chat + citations + history | ✅ | SSE, persisted citations, semantic cache, quotas, feedback |
| Auth (bcrypt, JWT, refresh, revocation, lockout) | ✅ | Rotation with theft detection; jti blacklist |
| Rate limiting (Redis, distributed-correct) | ✅ | All sensitive endpoints incl. auth; bounded fallback |
| Multi-tenancy | ✅ | Visibility model, guest space, scoped stats/conversations |
| Analytics & cost | ✅ | Per-model pricing, per-user scoping, daily series, cache savings path |
| Admin & audit | ✅ | Enterprise-gated console API + audit trail |
| Evals | 🔶 | Retrieval hit-rate works; faithfulness metric defined, CI wiring pending (Phase 2 tail) |
| CI pipeline / E2E frontend tests / storage abstraction / formats beyond PDF / reranker / connectors | ⬜ | Phases 2 (tail), 3, 4, 6, 7 |

---

## Phase 0 — Engineering Baseline (2 days)

**Objective:** make every later change measurable, lintable, and reproducible.

### Tasks
- Migrate test runner to **pytest** (keep `unittest`-style tests working via pytest
  compatibility), add `pytest-asyncio`, `pytest-cov`, `httpx` to dev deps.
- Add **ruff** (lint + format) and **mypy** configs; fix existing findings; add
  **pre-commit** hooks (ruff, ruff-format, mypy on changed files, detect-secrets).
- `Makefile` / `scripts/`: `make dev`, `make test`, `make lint`, `make eval`, `make up`,
  `make seed`.
- Config hardening in `config.py`:
  - Startup validator: refuse to boot when `JWT_SECRET` is the default in
    `DEBUG=False`, or when `DEBUG=False` with permissive CORS (`*`).
  - Add `ENVIRONMENT` setting (`local` / `staging` / `production`) gating debug
    behaviors (docstring in `/docs`, error detail exposure).
- Housekeeping: `.env` removed from git tracking (add to `.gitignore`), stale comment in
  `db/models.py` about "migration 0002" fixed, `.dockerignore` for backend/frontend.
- `docs/` skeleton (this set) and changelog file.

### Edge cases
| Case | Handling |
|---|---|
| Boot with default secret in production mode | Fail fast with explicit error, not silent weak auth |
| `.env` missing entirely | Sensible defaults for local dev; explicit failure only for production-required keys |
| Port already in use / stale PID | Clear startup error message |
| Python 3.11 vs 3.12 wheel differences (PyMuPDF) | Pinned versions with `pyproject.toml` constraints; CI matrix guards it |

### Testing additions
- Smoke test: app factory builds; config validator raises on bad production config.
- CI pipeline scaffold runs lint + mypy + tests on every push (full pipeline in Phase 2).

### Acceptance criteria
- `make lint && make test` green; pre-commit installed and passing on the repo.
- Production-config misconfiguration fails startup with a readable error.
- No secrets tracked in git.

---

## Phase 1 — Security Hardening & True Multi-Tenancy (5 days) — *P0, do first*

**Objective:** close every identified authorization/data-isolation gap and put
authentication on a production lifecycle.

### 1.1 Tenant-scoped retrieval (critical fix)
- Thread an **owner scope** through retrieval: `retrieve(db, query, owner_id=...)` adds
  `AND (d.owner_id = :owner OR d.visibility = 'public')` to both the vector leg
  (`retrieval.py`) and the FTS leg.
- `/api/search` requires auth (or explicit guest scope = public docs only).
- Chat passes the requester's scope; citations can only reference in-scope chunks.
- New `documents.visibility` column (`private` | `public`, default `private`) +
  migration; seed corpus marked `public` so the guest demo still works.

### 1.2 Guest mode policy (principle of least privilege)
- Guests: may search/chat over **public** documents only; may **upload** (demo) but
  uploads become private-orphaned with a quota; **may not** delete or retry anything.
- Ownership checks change from `if user and doc.owner_id and ...` to an explicit
  `require_document_access(user, doc, action)` helper: deny-by-default — delete/retry
  require an authenticated owner or admin; read requires owner, public visibility, or admin.
- Conversations with `owner_id = NULL` (guest-created) are visible **only** to
  unauthenticated sessions on the same browser session token (guest session id), never
  to other authenticated users. Guest conversations get a `guest_session_id` column.
- `/api/stats` scoped per user; global stats require admin role.

### 1.3 Authentication lifecycle
- **Refresh tokens:** short-lived access (30 min) + refresh (30 days, rotating, stored
  hashed in DB with device metadata); `POST /api/auth/refresh`, `POST /api/auth/logout`
  (revokes refresh family), refresh-token-reuse detection revokes the whole family.
- **Password reset:** request → time-limited single-use token (hashed at rest) → reset
  endpoint; email delivery abstracted behind a `Mailer` protocol (console logger in dev,
  SMTP adapter optional).
- **Account lockout:** exponential backoff after 5 failed logins per email+IP.
- JWTs gain `jti`; a Redis blacklist checks revoked access tokens on decode.

### 1.4 Distributed rate limiting & quotas
- Replace in-memory limiter with a **Redis sliding-window limiter** (sorted sets) —
  correct across workers/replicas, bounded memory, returns `Retry-After`.
- Limits: auth endpoints (10/min/IP), chat (30/min/user), upload (20/min/user),
  search (60/min/user). Per-user daily quotas configurable (`MAX_DAILY_QUESTIONS`,
  `MAX_STORAGE_MB_PER_USER`); exceeded → 429 with quota info.

### 1.5 Upload & file hardening
- Enforce max pages (e.g. 500), max dimensions, and **parse timeout** — reject
  "zip-bomb"/pathological PDFs (decompression ratio check, page-count sanity check via
  header-only parse before full ingest).
- Reject encrypted PDFs with a clear 422 message ("password-protected PDFs are not
  supported").
- Per-user storage quota check before writing bytes; atomic write (tmp + rename).
- Response error bodies sanitized: no stack traces, no internal paths (fix the raw
  exception streamed in `chat.py`'s error event).

### 1.6 Platform security headers & audit
- Middleware: `X-Content-Type-Options`, `X-Frame-Options`, strict `Referrer-Policy`,
  CSP for the SPA, `Content-Security-Policy` report-only first.
- **Audit log** table: actor, action (`auth.login`, `doc.upload`, `doc.delete`,
  `chat.query`…), resource id, IP, user agent, timestamp — written via middleware/events,
  admin-readable endpoint.

### Edge cases (enumerated)
| # | Case | Required behavior |
|---|---|---|
| 1 | Valid JWT but user deleted | 401, token treated as invalid (user lookup each request or `jti` invalidation on delete) |
| 2 | Refresh token used twice (theft signal) | Reject + revoke entire token family, audit-log the event |
| 3 | Document deleted while Celery task mid-processing | Task detects missing row / tombstone, aborts cleanly, no orphan chunks |
| 4 | Two concurrent uploads with same filename | Independent UUID storage; duplicate-content SHA-256 detection offers "same document re-indexed" note |
| 5 | Upload racing user quota check (TOCTOU) | Quota enforced again at write time; on exceed, roll back row + file |
| 6 | Guest deletes/retries any document | 401/403 — regression-tested |
| 7 | Authenticated user deletes another user's private doc | 404 (not 403 — don't leak existence) for private docs |
| 8 | Chat on a conversation owned by someone else | 404, audit-logged |
| 9 | `visibility=public` doc deleted while cited in old conversation | Old messages keep stored citation snapshot (already JSON) — verify render |
| 10 | Rate limiter: Redis down | Fail **open** for reads, fail **closed** for auth/upload (configurable), alert log |
| 11 | Token in `Authorization: bearer` (lowercase) / extra spaces | Strict but tolerant parsing per RFC; malformed → anonymous, not 500 |
| 12 | Password reset token replay / expiry | Single-use flag + 30-min expiry; replay logs security event |
| 13 | Email case/unicode variants (`A@x.com` vs `a@x.com`) |Normalized to lowercase NFKC at registration and login |
| 14 | Login during lockout with correct password | Still locked; 429 with retry-after; unlock only by timer/admin |

### Security controls
Ownership deny-by-default helper; tenant scope in every retrieval SQL; refresh rotation
+ reuse detection; lockout; Redis rate limits on auth; quotas; sanitized errors; audit
log; security headers; secrets validator; password policy (min length 10, breach-list
check optional).

### Testing additions (must be written WITH the feature)
- **Tenant isolation regression suite:** user A uploads private doc → user B (and guest)
  chat/search/list — assert zero A-chunks surface anywhere (citations, search, stats,
  conversation list). This suite fails on the current code — it's the reason the phase exists.
- Ownership matrix tests (guest/user/owner/admin × read/upload/delete/retry/list).
- Auth lifecycle: refresh rotation, reuse detection, logout revocation, expired tokens,
  lockout counters, reset token single-use.
- Rate limiter: integration test against Redis; cross-"worker" correctness; `Retry-After`.
- Upload hardening: encrypted PDF, 10k-page hostile PDF (fixture), quota exceeded mid-flight.

### Acceptance criteria
- Tenant isolation suite green; guest cannot mutate anything; all retrieval SQL carries
  the owner scope (enforced by a unit test asserting the SQL fragment).
- Refresh flow demonstrable via OpenAPI; logout actually revokes.
- No endpoint accepts unauthenticated destructive action.
- Audit rows written for every action in the matrix; security headers present in responses.

---

## Phase 2 — Testing Rig & CI/CD (4 days)

**Objective:** make quality *provably enforced*, not asserted. (Details in
[TESTING_STRATEGY.md](TESTING_STRATEGY.md) — summarized here for the roadmap.)

### Tasks
- **Integration harness:** pytest fixtures spinning real Postgres + Redis
  (testcontainers or dedicated compose profile), Alembic migrations applied per session,
  per-test transaction rollback; fake embedding/LLM servers (deterministic vectors +
  scripted completions) so the full pipeline is testable offline.
- **API integration tests** over `httpx.AsyncClient` against the real app: full journey
  (register → upload → poll → search → chat → citations → delete).
- **Property-based tests (hypothesis):** chunker invariants (any text → chunks ≤ size,
  overlap respected, character coverage with no loss), RRF fusion invariants
  (deterministic, order stability, bounded scores).
- **Frontend:** Vitest + React Testing Library (auth modal, upload states, citation
  cards, SSE consumer with mocked stream); Playwright E2E against docker-compose
  (upload → ready → ask → streamed answer + citation rendered).
- **CI (GitHub Actions):** lint (ruff/mypy) → backend tests (coverage gate ≥ 80% on
  `app/services` + `app/api`) → frontend tests → docker builds → **eval gate**.
- **Eval gate:** `scripts/run_eval.py` extended to also score **faithfulness and answer
  relevance** (currently `_simple_overlap_score` is defined but never called — wire it
  in, add LLM-graded scoring behind a flag), exit non-zero below thresholds
  (hit@3 ≥ 80%, faithfulness ≥ 0.6), publish results as a CI artifact.
- **Load smoke:** k6/locust script — 50 concurrent chats, 10 uploads; baseline p95
  recorded and asserted loosely (no N+1 regressions).

### Edge cases covered by new tests
Encrypted/corrupt/truncated PDFs; `%PDF-` header with garbage body; 0-page PDF;
PDF with images only (OCR path, marked slow); embedding service down mid-ingestion
(batch N fails → BM25-only completion + status note); LLM dies mid-stream (client
receives `error` event, partial answer persisted as interrupted, conversation usable);
question in a different language; question that matches nothing (graceful
"not enough information" path); concurrent chats on one conversation (message ordering).

### Acceptance criteria
- One command (`make verify`) runs the whole gate locally; CI runs it per push/PR.
- Coverage ≥ 80% services/api; eval gate fails CI below thresholds; E2E green in CI.

---

## Phase 3 — Ingestion & Document Processing Excellence (5 days)

**Objective:** ingest anything a client throws at it, correctly — structure-aware
chunking, multi-format, multi-language, resilient.

### 3.1 Format expansion (registry pattern: `Extractor` per mime type)
- **DOCX** (python-docx): paragraphs + heading hierarchy + tables; **XLSX/CSV**: sheet →
  row-group text serialization with header context; **PPTX**: slide-wise text + speaker
  notes; **TXT/MD**: direct (markdown heading-aware); **HTML**: readable-text extraction;
  **EPUB** optional.
- Upload API accepts per-format allowlist with size limits per type; response reports
  detected type. Frontend dropzone updated accordingly.

### 3.2 Chunking v2 (structure-aware)
- Heading-aware segmentation: chunk carries `section_path` (e.g. `Handbook > Leave >
  Parental Leave`) stored as metadata; retrieval results and citations display it.
- **Token-based sizing** (tiktoken or estimate upgrade) instead of raw chars; per-format
  tuning (tables stay whole where possible; rows grouped with header repeated).
- Min-chunk merging (avoid 40-char orphan chunks), overlap measured in tokens,
  configurable per extractor.
- Store `chunk_metadata` JSON (section path, extractor, method, language, ocr confidence).

### 3.3 OCR excellence
- Language packs config (`OCR_LANGUAGES=eng+deu`), auto-detect option per document.
- Preprocessing: grayscale (already), thresholding/deskew for skewed scans; adaptive DPI
  (300 for low-quality scans).
- Per-page OCR timeout + confidence score from Tesseract stored per chunk; low-confidence
  pages flagged in the document detail UI ("page 7 OCR confidence 61% — verify").
- Parallel OCR across pages in the worker (process pool), with cancellation on doc delete.

### 3.4 Resilience & reindexing
- **Embedding model versioning:** chunks record `embedding_model` + `dim`; switching
  models triggers a **background re-embed job** (per document, progress-tracked) instead
  of silent dim mismatch; legacy chunks remain searchable via BM25 during reindex.
- SHA-256 document dedup (re-upload = re-index or attach existing).
- Failed-page tolerance: one corrupt page doesn't fail the document; per-page errors
  recorded and shown.
- Huge documents: streaming page iteration (already), batched DB inserts (already),
  configurable ingest concurrency; cancellation endpoint (stop processing).

### Edge cases
| Case | Behavior |
|---|---|
| Encrypted PDF (user password / owner password) | Detected pre-ingest, explicit 422 message |
| PDF whose text layer exists but is garbage ( CID/fonts broken) | Density heuristic + garbage-character ratio check → auto-OCR that page |
| Rotated/scanned pages (90°, upside down) | OSD (orientation detection) before OCR |
| XLSX with 200k rows | Row-group serialization + row cap with note; never OOM the worker |
| DOCX with nested tables/text boxes | Extractor flattens in reading order; text boxes appended with marker |
| Duplicate upload of identical file | SHA-256 match → re-use existing chunks or re-index per user choice |
| Embedding provider dims change mid-fleet | Reindex job reconciles; mixed-dim searches excluded per leg, never crash |
| OCR binary missing entirely (slim deploy) | Document fails with actionable error; UI shows "OCR not available in this deployment" |
| File renamed with wrong extension (`.pdf` that is DOCX) | MIME sniff beyond magic bytes; route to correct extractor or reject |
| RTL / CJK documents | `to_tsvector` config per language ('simple' fallback), chunker respects no-space scripts |

### Testing additions
- Golden fixtures per format (small synthetic DOCX/XLSX/PPTX with known text) →
  extraction assertions.
- Chunking v2: section-path correctness on a nested-heading fixture; token-budget tests.
- Reindex job: model switch → old docs re-embedded → retrieval uses new vectors.
- OCR: synthetic scanned fixture (render text to image, embed in PDF, assert OCR
  recovers ≥ 90% of tokens) marked slow.

### Acceptance criteria
- DOCX/XLSX/PPTX/TXT/MD uploadable and answerable in chat with correct citations;
  section paths visible in citations; model-switch reindex verified end-to-end.

---

## Phase 4 — Retrieval & Answer Intelligence (5 days)

**Objective:** measurably better answers — reranking, query understanding, grounded
answers, and honest failure.

### 4.1 Reranker stage
- After RRF, top ~20 candidates re-scored by a cross-encoder (bge-reranker via Ollama
  or a hosted API behind the same provider abstraction); configurable on/off and
  candidate pool size; latency budget enforced (rerank ≤ 300 ms, else skip).
- A/B flag + eval comparison recorded (hit@k and faithfulness with vs without).

### 4.2 Query understanding
- **Follow-up resolution:** conversation context used to rewrite standalone questions
  ("What about parental leave?" → resolved form) — small LLM call, cached.
- **Multi-query expansion** for vague questions (2–3 reformulations retrieved, fused).
- Language detection: non-English questions retrieve with per-language FTS config and
  prompt instruction to answer in the question's language.

### 4.3 Context assembly & grounding
- Token-budget context packing (model context window aware; fills highest-ranked chunks
  until budget, never truncates mid-chunk silently — reports dropped count in `meta`).
- **Citation validation:** post-generation check that every `[n]` in the answer maps to
  a real provided source; unresolvable markers stripped; answers citing nothing while
  context exists get flagged in `QueryLog` (grounding metric).
- Prompt-injection defense: document excerpts are wrapped in explicit delimiters, system
  prompt hardening, plus an output filter for instruction-leak patterns; eval cases for
  "ignore previous instructions" planted in documents.

### 4.4 Caching & honest failure
- Semantic cache: frequently-asked questions answered from cache (embedding similarity
  ≥ threshold + same doc scope), TTL'd, invalidated on document change; adds `cached: true`.
- Failure honesty: LLM unavailable → explicit degraded mode banner in UI (search-only),
  not fake answers; partial stream failures handled as in Phase 2.

### Edge cases
| Case | Behavior |
|---|---|
| Question matches nothing in scope | Exact refusal sentence + "try uploading…" hint; no hallucinated fill |
| Reranker times out / unavailable | Skip rerank, log, answers still flow (graceful degradation ladder: rerank → RRF → BM25) |
| Cache hit on stale index (doc deleted/changed) | Scope+doc-version key invalidates cache; deleted docs never cited |
| Prompt injection planted in document | Filter + eval regression; model instructed context is data, not instructions |
| Answer cites [5] but only 4 sources | Validator strips/corrects; logged |
| Extremely long question (> 2k tokens) | Truncate + 422 guidance |
| Two users ask same question, different scopes | Cache key includes scope hash — no cross-tenant leakage |
| Embedding service down at question time | BM25-only mode, `methods` shows it, UI banner "degraded retrieval" |

### Testing additions
- Reranker unit tests with fake scorer; pipeline degradation ladder tests (each service
  killed in turn → assert fallback).
- Grounding/citation validator tests (fake citations, injection fixtures).
- Eval dataset extended: injection cases, multi-hop questions, follow-up resolution cases;
  thresholds added to CI gate.

### Acceptance criteria
- Eval shows ≥ +5 pts hit@1 with reranker on the golden set (or documented decision to
  keep it off); citation validator live; injection eval cases pass.

---

## Phase 5 — Product Features (6 days)

**Objective:** the client-visible experience that closes deals.

### 5.1 Citation viewer (flagship)
- PDF.js viewer panel: click a citation → document opens at the cited page → the source
  chunk's text **highlighted** (store chunk bounding boxes or page-text offset mapping
  at ingest to locate highlights).
- Works for OCR pages too (highlight = text-match fallback when boxes unavailable).

### 5.2 Feedback loop
- 👍/👎 + optional comment per assistant message; stored; feeds the eval dataset
  (down-voted Q&A become regression cases); admin dashboard of feedback trends.

### 5.3 Workspaces & RBAC
- `Workspace` entity; documents/conversations belong to workspaces; members have roles
  (`owner`/`member`/`viewer`); retrieval scope = workspace membership ∪ public.
- Migration maps existing user docs to a personal workspace.
- Workspace switcher in the UI; per-workspace stats.

### 5.4 Admin panel & API keys
- Admin role + `/admin` area: users (disable, quota edit), documents (failed triage,
  bulk reprocess), audit log viewer, system health.
- **API keys** (hashed at rest, scoped, revocable) for programmatic access; per-key
  rate limits and usage lines in stats. Enables "product" positioning, not just an app.

### 5.5 UX polish
- Pagination + filters on documents/conversations; document detail page (pages, chunks,
  OCR confidence, per-page errors, reprocess); drag-drop multi-upload with queue;
  ingestion-complete toast/notification (SSE or polling); markdown export of a
  conversation; share conversation read-only link (tokenized).

### Edge cases
| Case | Behavior |
|---|---|
| Citation clicked after document replaced by re-upload | Version check → "document has changed" notice, fall back to page-level view |
| Viewer on scanned page (no text layer) | Page render + OCR text side-by-side |
| Member removed from workspace mid-session | Next scoped query excludes workspace; open conversation read-only |
| API key leaked | Revoke instantly; keys hashed; audit shows usage |
| Viewer role attempts upload | 403 with clear UI state |
| Feedback on deleted conversation/message | Idempotent no-op |
| Shared link after conversation deleted | 410 Gone page |

### Testing additions
- RBAC matrix tests (role × action × workspace); API-key auth path tests; viewer
  highlight mapping unit tests; E2E: click citation → viewer opens at page.

### Acceptance criteria
- Demo script possible entirely in UI: multi-format upload → ask → click citation →
  highlighted source page → feedback → admin sees the feedback + audit trail.

---

## Phase 6 — Platform, Observability & Ops (4 days)

**Objective:** run it like infrastructure, not a laptop app.

### Tasks
- **Storage abstraction:** `StorageBackend` protocol (local disk now; S3/MinIO adapter);
  uploads/chunks of bytes flow through it; compose profile adds MinIO for local S3 mode.
- **Observability:** Prometheus metrics (`/metrics`: request latency histograms per
  route, ingestion duration, retrieval leg latencies, token usage, cache hits), Grafana
  dashboard JSON in repo, Sentry hook (DSN-gated), request-ID propagation into logs
  (structured JSON logging).
- **Celery hardening:** `acks_late`, task time limits, retry with exponential backoff,
  dead-letter handling, worker healthcheck in compose; beat schedule for cleanup
  (orphaned uploads, expired sessions, old query logs per retention policy).
- **Production compose profile:** gunicorn/uvicorn workers config, non-root containers,
  resource limits, **HTTPS via Caddy/Traefik** (auto-certs), secrets via env files,
  Postgres backup/restore scripts + documented restore drill.
- **Data lifecycle:** retention policy config (query logs N days), full account export
  + hard delete (GDPR-style), documented.

### Edge cases
| Case | Behavior |
|---|---|
| S3 unavailable at upload | 503 with retry guidance; no half-written rows (write-ahead order: storage → DB) |
| Worker killed mid-task | `acks_late` re-delivers; task idempotent (existing chunks cleared per document before insert) |
| Postgres failover/restart | Pool `pool_pre_ping` (already) + reconnect backoff; inflight chats error cleanly |
| Disk full (local mode) | Upload fails with actionable error; audit-logged |
| Log/metric cardinality explosion | Route labels bounded; user IDs as hashed tags |
| Restore drill | Documented, scripted, tested restore of pgdata volume + uploads bucket |

### Testing additions
- Storage adapter contract tests (same suite run against local + MinIO);
  idempotent-task re-delivery test (kill worker mid-ingest in test);
  metrics endpoint assertions; backup/restore integration test.

### Acceptance criteria
- `docker compose --profile production up` serves over HTTPS with non-root containers;
  Grafana shows live dashboards; restore drill documented and rehearsed in README.

---

## Phase 7 — Connectors & Automation (5 days, optional per client)

**Objective:** the #1 enterprise request — "it should ingest from where our documents live."

### Tasks
- **Connector framework:** `Connector` protocol (`list_changes`, `fetch`, `sync_state`);
  per-connector credentials encrypted at rest (Fernet with app master key).
- **Filesystem watch folder** connector (path → poll/hash change detection).
- **S3 bucket** connector; **Google Drive** + **SharePoint/OneDrive** (OAuth app flow,
  delta APIs) — behind the framework, delivered as compose-enabled optional services.
- **Incremental sync:** content-hash change detection → re-ingest only changed files;
  deletions propagate (document soft-delete); scheduled via Celery beat; per-connector
  sync status + error surfacing in UI.

### Edge cases
| Case | Behavior |
|---|---|
| Connector creds expired (OAuth refresh) | Marked paused + notified, not silent failure |
| File modified during fetch | Hash-after-fetch verify; retry once; skip with note on mismatch |
| 10k files initial sync | Bounded queue, progress UI, resumable sync state |
| File deleted at source | Soft-deleted in app, removed from retrieval, restorable for N days |
| Two connectors racing same document | Content-hash dedup attaches, no duplicate ingestion |
| Watch folder on network share that disappears | Error state + backoff, auto-resume |

### Testing additions
- Fake connector fixtures; incremental-change test (modify/add/delete file → expected
  re-ingest set); credential encryption round-trip tests; resumable sync after kill.

### Acceptance criteria
- Demo: drop a file in a watched folder → it appears indexed in DocuQuery within the
  sync interval, answerable with citations; deleting it removes it from answers.

---

## Phase 8 — Ship: Case Study, Sales Kit & Handover (2 days)

- README finalized as a **case study** (problem → architecture → metrics → security);
  architecture doc (client-facing) updated to final state; recorded 3-minute demo video
  script; seeded demo environment script (`make seed-demo` — corpus + demo accounts +
  pre-run eval report).
- UAT checklist + deployment guide (single VPS, Docker; cost estimate per tier);
  Upwork proposal one-pager derived from [PRODUCT_OVERVIEW.md](PRODUCT_OVERVIEW.md).
- Version tag, changelog, license decision per client deal.

---

## Dependency graph & sequencing

```
Phase 0 ─► Phase 1 ─► Phase 2 ─► Phase 3 ─► Phase 4 ─► Phase 5 ─► Phase 8
                    │                        │
                    └────────► Phase 6 ──────┴──────► Phase 7 ──► Phase 8
```

- Phases 1→2 are strictly first (security then proof).
- Phase 6 can run parallel to Phase 4/5 (different files, no conflicts).
- Phase 7 is optional per engagement; everything else is assumed for "production grade".

## Effort summary

| Phase | Days | Cumulative |
|---|---|---|
| 0 — Baseline | 2 | 2 |
| 1 — Security & tenancy | 5 | 7 |
| 2 — Testing & CI | 4 | 11 |
| 3 — Ingestion excellence | 5 | 16 |
| 4 — Retrieval intelligence | 5 | 21 |
| 5 — Product features | 6 | 27 |
| 6 — Platform & ops | 4 | 31 |
| 7 — Connectors (optional) | 5 | 36 |
| 8 — Ship & handover | 2 | 38 |

## Definition of "production grade" (final gate)

1. No unauthenticated destructive action; tenant isolation proven by regression suite.
2. Full journey covered by integration tests; coverage gate + eval gate + E2E in CI.
3. Every format promised in the product doc ingestible, including scanned and
   multi-language, with OCR confidence surfacing.
4. Answers are grounded, citation-validated, refuse honestly, and degrade gracefully
   (reranker → RRF → BM25 → search-only) — never fake an answer.
5. Observable (metrics, logs, audit), operable (HTTPS, backups, retention, healthchecks),
   and extensible (storage, LLM, embedding, connector, extractor — all behind interfaces).
