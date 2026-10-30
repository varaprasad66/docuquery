# DocuQuery — Testing & Quality Strategy

> **Audience:** clients and technical stakeholders. How quality is *proven* at every
> level — and how that proof is enforced automatically. The suite described here is
> **implemented and running**: 56 unit tests + 52 integration tests = 108 automated
> tests, all green.

---

## 1. Philosophy

A RAG system has two failure modes, and they need different tools:

1. **Software failure** — an endpoint crashes, a document ingests wrongly, a permission
   leaks. Caught by **tests**.
2. **Quality failure** — the software "works" but answers get worse after a change.
   Caught by an **evaluation benchmark** (a golden dataset scored on every release).

DocuQuery enforces both. Nothing ships because a human "checked it once."

---

## 2. The four test layers (implemented)

### Layer 1 — Unit tests (56 tests, < 2 seconds)
Individual logic verified in isolation:

- **Chunking engine:** paragraph-aware splitting, sentence-boundary hard splits,
  overlap carrying, pathological inputs (unbroken strings, URLs, tables).
- **Fusion algorithm (RRF):** deterministic ranking, score bounds, correct merge of
  duplicate hits from both search legs, top-k behavior.
- **Access-control matrix:** every role (guest / user / owner / admin) × every action
  (read / mutate) across document visibilities — the deny-by-default rules as tests.
- **Token lifecycle:** creation, decoding, expiry, tamper rejection, **revocation
  blacklist** (revoked tokens stop working; expired blacklist entries self-clean;
  bounded memory).
- **Rate limiting & lockout:** sliding-window behavior, per-identity isolation,
  `Retry-After`, bounded memory; lockout triggers after N failures, clears properly,
  never leaks across identities.
- **Cost calculation:** per-model pricing math; zero-priced local models cost zero.
- **Edition gating:** feature surfaces activate in the correct edition tier only.
- **Input validation schemas:** boundary values and malformed requests rejected.

### Layer 2 — Integration tests (52 tests, ~35 seconds, real database)
The application is started **for real** — a disposable PostgreSQL test database with
the full schema, real HTTP requests over the ASGI stack, real Redis-backed behavior
falls back to deterministic in-process backends, and AI providers replaced by
deterministic fakes (same input → same vectors/answers) so tests are fast, offline,
and reproducible.

**The tenant-isolation regression suite (the critical one):** user A uploads a private
document; user B — and an unauthenticated guest — then search, chat, list, fetch,
patch, and delete. The suite asserts that **zero** of A's content surfaces anywhere:
not in search results, not in chat citations, not in document listings (404-shaped, so
existence isn't leaked), not in stats, not in conversation history — and that A's
document survives every unauthorized mutation attempt. *This suite fails on
pre-hardening code; it is the reason the hardening exists.*

Also covered at this layer:

- **Full product journey:** register → upload → process → search → chat with streamed
  citations → delete → content disappears from search.
- **Auth lifecycle:** registration policy (weak passwords rejected), duplicate email,
  wrong password, **lockout after 5 failures (even with the correct password)**,
  refresh-token rotation, **replay detection revoking the whole token family**,
  logout revoking the access token, deactivated-user enforcement, case-insensitive login.
- **Ingestion edge cases:** non-PDF rejection, empty files, **PDF forgery** (magic
  header + garbage body → clean `failed` state, not a crash), **encrypted PDFs**
  rejected with a readable message, delete cascade removing content from search,
  **embedding-provider outage → automatic keyword-only degraded mode** (results
  still flow, method labeled), document-filtered search.
- **Chat pipeline:** SSE event structure (meta → citations → tokens → done), citation
  correctness (document + page), history persistence, **semantic cache hit on repeat
  questions (zero prompt tokens)**, **cache scoped per user**, **cache invalidated on
  document change**, daily question quotas (429), feedback round-trip and ownership,
  **sanitized streaming errors** (internal details provably never reach the client).
- **Analytics scoping:** user sees own counts only, guest scope separate, admin global;
  daily series, cost fields, cache hit-rate, feedback counts.
- **Admin console:** admin-only access (404 for non-admins — surfaces not advertised),
  user disabling (immediately effective), self-lockout prevention, audit trail rows
  recorded for real actions, global overview, document reprocessing.
- **API hardening:** search rate limiting returns 429 past the threshold; security
  headers on every response; request-ID echo for correlation.

### Layer 3 — End-to-end tests *(planned phase)*
Playwright drives a real browser against the Dockerized stack: upload → ready → ask →
streamed answer + citation card; login flows; (roadmap) click a citation → viewer opens
at the cited page.

### Layer 4 — Evaluation benchmark *(golden set, CI gate)*
A golden dataset of question → ground-truth pairs across contracts, HR policy,
invoices, and dense academic text, scored per release:

| Metric | Current baseline |
|---|---|
| Retrieval hit rate @ top-1 | 85% |
| Retrieval hit rate @ top-3 | 95% |
| Retrieval hit rate @ top-6 | 100% |
| Avg retrieval time | ~8 ms (local) |
| Faithfulness / answer relevance | scored; CI gate *(wiring phase)* |

Down-voted user feedback folds into the golden set, so real-world failures become
permanent regression cases.

---

## 3. How the suite runs

```
scripts/run_tests.py
├── Phase 1: Unit tests        (tests/)            — no infrastructure needed
└── Phase 2: Integration tests (tests_integration/) — fresh process, disposable
                                                     PostgreSQL test database
                                                     (auto-created & migrated),
                                                     deterministic fake AI
```

Exit code is non-zero on any failure, so the whole gate drops into CI as-is.
Integration tests **skip cleanly** (never false-fail) when Postgres is unreachable,
and never touch the development database — they run against `docuquery_test`,
recreated fresh per run.

---

## 4. Testing the hard cases explicitly

Beyond the happy path, the suite deliberately covers the cases that break real
deployments:

| Category | Examples covered ✅ |
|---|---|
| **Hostile files** | Corrupt/truncated PDFs, header-forged fakes, encrypted PDFs, empty files |
| **AI provider outages** | Embeddings down → keyword-only degraded mode (labeled in results); LLM dies mid-answer → client notified with a sanitized message, internals provably not leaked |
| **Security regressions** | Tenant isolation (14 dedicated tests), guest over-privilege, token replay, lockout bypass, rate-limit bypass, non-admin admin access |
| **Concurrency & state** | Document deleted mid-journey, cache invalidation on corpus change, deactivated users mid-session |
| **Data boundaries** | Empty index, repeated questions (cache), per-user quota limits, cross-user statistics |

---

## 5. What this means for you as the client

- Changes arrive **with their proof**: every feature lands with tests, and the whole
  gate re-runs automatically.
- **Data isolation is locked in by regression tests**, not by good intentions.
- Security behavior you rely on (lockout, revocation, quotas, rate limits) is
  **asserted by automated tests** in every release.
- You get an auditable quality trail: test results and benchmark reports per release.

---

*Related: [ARCHITECTURE.md](ARCHITECTURE.md) · [SECURITY_MODEL.md](SECURITY_MODEL.md) · [PRODUCT_OVERVIEW.md](PRODUCT_OVERVIEW.md)*
