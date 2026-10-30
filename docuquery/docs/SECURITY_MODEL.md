# DocuQuery — Security Model

> **Audience:** clients, security reviewers, procurement. How DocuQuery protects data,
> enforces access control, and resists abuse — at a conceptual level, without exposing
> implementation details. Status: ✅ implemented and test-covered · 🔶 planned phase.

---

## 1. Security design principles

1. **Deny by default.** Every document, conversation, and statistic is inaccessible
   unless a rule explicitly allows it for the requesting identity.
2. **Isolation by construction.** User data is scoped at the data-access layer, not by
   hiding it in the UI — every search and retrieval query carries the requester's scope.
3. **Fail honest.** Errors never leak internals (stack traces, paths, queries) to clients;
   the system prefers explicit refusal over permissive fallbacks.
4. **Everything sensitive leaves a trail.** Security-relevant actions are audit-logged.
5. **Boring, standard mechanisms.** Bcrypt, JWT, TLS, parameterized SQL, rate limits —
   no home-grown cryptography or exotic trust assumptions.

---

## 2. Authentication

| Control | Status | Description |
|---|---|---|
| Password storage | ✅ | Bcrypt with per-password salt and hardened cost factor; plaintext never stored or logged |
| Session tokens | ✅ | Signed access tokens (JWT) with unique token IDs, carried in the `Authorization` header; expiry enforced |
| Token lifecycle | ✅ | Refresh-token rotation with **reuse detection** (a replayed token revokes its whole family — theft signal), logout revokes both the refresh token and the access token until natural expiry |
| Brute-force resistance | ✅ | Account lockout after 5 failed logins (per account+IP, 15-minute window) — even the correct password is refused while locked; strict rate limits on login/register |
| Password policy | ✅ | Configurable minimum length plus letter+digit requirement, validated at registration |
| Disabled accounts | ✅ | Deactivated users are treated as anonymous immediately — their tokens stop working |
| Login identity normalization | ✅ | Emails lowercased so case variants can't create duplicate identities; timing-equalized credential checks |
| Secret hygiene | ✅ | Startup validation refuses production boot with default/weak secrets; secrets supplied via environment only |

**Guest mode** is a deliberate, constrained capability: guests can explore the product
over public documents and their own uploads (the "guest space"), which no authenticated
account can see. Guests can never read private data, and can never delete or modify
anything — enforced and regression-tested.

---

## 3. Authorization & tenant isolation

The core guarantee: **a user's documents and conversations are visible only to that
user** (plus explicitly public content) — and this is enforced inside the retrieval
engine, not the UI layer.

- **Document access** — checked per request against ownership and visibility
  (`private`/`public`) with role awareness; private documents of other users return
  "not found" rather than "forbidden", so even the existence of content isn't leaked.
- **Retrieval scope (the critical control)** — both search legs (semantic and keyword)
  filter on the requester's scope *inside the query itself*. A user's question can
  never retrieve another tenant's document fragments, even indirectly through
  citations. **Proven by a dedicated regression suite** that fails the build on any
  leakage (search, chat, listings, stats, conversations, deletion paths).
- **Conversations & history** — strictly per user; guest conversations are invisible to
  authenticated users and vice versa; cross-user access attempts return not-found.
- **Administration** — a separate admin role gates user management, global analytics,
  and the audit log; lower editions and non-admins receive "not found" (surfaces are
  not advertised).
- **Workspaces & roles** 🔶 *(planned phase)* — team folders with owner/member/viewer roles.

---

## 4. Data protection

| Area | Approach |
|---|---|
| **In transit** | TLS termination at the reverse proxy (automatic certificate management in the production profile); all client↔API traffic over HTTPS |
| **At rest** | Documents and vectors live in your PostgreSQL instance and file storage — self-hosted by default; *(roadmap)* object-storage adapter with encrypted buckets; *(roadmap)* connector credentials encrypted with an application master key |
| **Privacy by deployment** | In **private mode** the AI models run inside your infrastructure — no document content or question text leaves your network. Cloud mode is an explicit, opt-in configuration choice |
| **Data minimization** | Only what's needed is stored: document content, its chunks/vectors, and chat history. No third-party analytics on document data |
| **Deletion** | Deleting a document removes its file, chunks, vectors, and indexes (database-level cascade) — verified end-to-end by tests; *(roadmap)* full account export, configurable retention |
| **Backups** | *(roadmap)* Scripted PostgreSQL backup/restore with a documented, rehearsed restore procedure |
| **Cache safety** | Cached answers are scoped per tenant and invalidated wholesale whenever any document changes — stale or cross-tenant answers can never be served |

---

## 5. Input safety & abuse resistance

| Threat | Defense |
|---|---|
| Malicious files | Content-type and integrity validation before processing; encrypted/corrupt/forged PDFs rejected (✅ test-covered); size limits; per-user upload quotas ✅ |
| Injection (SQL) | All database access uses parameterized statements via the ORM/query layer — no string-built SQL |
| Cross-site scripting | React's default output encoding; security headers on every response (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`) ✅ |
| Prompt injection via documents | Retrieved document text is delivered to the model as delimited *data* with hardened instructions 🔶 *(planned phase: benchmark cases with planted payloads)* |
| Rate abuse / DoS-lite | ✅ Distributed rate limiting (Redis-backed with graceful fallback) on chat, search, upload, and auth endpoints — correct across replicas, with `Retry-After`; per-user daily quotas ✅ |
| Error leakage | ✅ Client-facing errors sanitized to safe messages (streaming included); internal detail only in server logs; every response carries a correlation request ID |
| Fake-file uploads | Magic-byte and structure checks, not extension trust; storage paths are server-generated |

---

## 6. Auditability & operations

- **Audit trail** ✅ — security-relevant events (registrations, logins, failed logins,
  logouts, uploads, deletions, visibility changes, admin actions) recorded with actor,
  action, resource, IP, and user agent; admin-readable.
- **Query analytics** ✅ — every question logs latency, token usage, cost, and the
  sources used, giving traceability from an answer back to exactly what fed it.
- **Request correlation** ✅ — every response carries an `X-Request-ID` echoed from the
  client or generated server-side, for incident forensics across logs.
- **Health checks** ✅ — the API exposes a health endpoint verifying database and AI
  provider reachability, used by the orchestrator for self-healing deployment.

---

## 7. Security roadmap summary

| Phase | Delivers |
|---|---|
| **Shipped (hardening core)** | Tenant-scoped retrieval + regression suite; guest least-privilege; refresh rotation & revocation; lockout; distributed rate limits incl. auth; quotas; encrypted/forged file rejection; sanitized errors; security headers; request IDs; audit log; admin role |
| *Planned phase* | Prompt-injection benchmark cases; workspaces & RBAC roles; object storage with encryption; connector credential encryption |
| *Planned phase* | TLS automation guidance, secrets management, backup/restore drill, data retention automation |

---

## 8. Compliance posture

DocuQuery's architecture maps naturally onto common requirements:

- **Data residency / GDPR-style control** — self-hosted deployment keeps all document
  data inside the client's infrastructure; cascade hard-delete supports data-erasure
  requests.
- **Traceability** — audit log + per-question source traceability support review and
  incident investigation.
- **Access-control evidence** — the permission-matrix and tenant-isolation regression
  suites provide demonstrable, test-enforced access control rather than policy
  documents alone.

For specific certifications (SOC 2, ISO 27001), the deployment inherits the hosting
organization's controls; the application layer provides the technical foundations above.

---

*Related: [ARCHITECTURE.md](ARCHITECTURE.md) · [TESTING_STRATEGY.md](TESTING_STRATEGY.md) · [PRODUCT_OVERVIEW.md](PRODUCT_OVERVIEW.md)*
