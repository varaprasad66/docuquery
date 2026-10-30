# DocuQuery — Product Overview & Editions

> **Client-facing one-pager.** What the product does, the three editions, and what each
> includes. No code, no internals — pair with [ARCHITECTURE.md](ARCHITECTURE.md) on calls.

---

## The problem

Every organization sits on hundreds of documents — contracts, policies, invoices,
reports, scanned paper — and every employee pays the same tax daily: hunting through
folders and PDFs to find one clause, one number, one policy line. Generic chatbots can't
help because they don't know your documents — and they **make things up** when they don't.

## The solution

**DocuQuery** is a private document intelligence platform. Your team uploads documents
(including scanned paper), asks questions in plain language, and gets **instant answers
with receipts**: every claim cites the exact document and page it came from.

Three promises, in every edition:

1. **Grounded answers, never inventions.** The system answers only from your documents
   and says "I don't have that in the documents" when it doesn't — by design.
2. **Your data stays yours.** Runs entirely inside your infrastructure; documents never
   leave your network in private deployment mode.
3. **Scanned paper works.** Built-in OCR means photo-quality scans are as searchable as
   digital PDFs — mixed documents handled automatically, page by page.

---

## Editions — pick the depth you need

### 1) DocuQuery Starter — "Ask your documents"

For teams that want the core capability deployed fast.

| Included | Detail |
|---|---|
| PDF ingestion with live progress | Drag-and-drop upload, 0–100% processing state, retry on failure |
| Automatic OCR fallback | Scanned pages recognized automatically — no manual flags |
| Hybrid smart search | Meaning-based + exact-term search fused (finds both "parental leave policy" and "invoice INV-2026-089") |
| Streaming answers with citations | Word-by-word answers, `[1]`, `[2]` markers linked to source cards (document · page · excerpt) |
| Conversations | Per-user chat history with citations preserved |
| User accounts | Secure registration/login; guest demo mode for evaluation |
| One-command deployment | `docker compose up` — full stack, health-checked |
| **Tenant isolation** | Users only ever see their own + public documents — enforced inside the retrieval engine itself |

**Best for:** pilots, internal knowledge bases, portfolio demos, first RAG integration.

### 2) DocuQuery Professional — "Everything Starter, plus cost control & analytics"

For teams running DocuQuery as day-to-day infrastructure and caring about spend.

Everything in Starter, plus:

| Included | Value |
|---|---|
| **Usage analytics dashboard** | Per-question latency, token usage, cost tracking; 14-day daily usage series |
| **Cost optimization: semantic answer cache** | Repeated questions are answered instantly at zero model cost; cache invalidates automatically when documents change |
| **Cost optimization: quotas** | Configurable daily question & upload limits per user |
| **Configurable model economics** | Per-model pricing configuration so cost reflects your actual provider bills |
| **Answer feedback loop** | 👍/👎 + comments on every answer — real-world failures become improvement targets |
| **Search within specific documents** | Restrict any search or chat to selected documents |
| **Strict per-user scoping** | Stats and history are per-user; admins see global |

**Best for:** production internal tools, multi-user deployments, cost-sensitive operations.

### 3) DocuQuery Enterprise — "Governance, admin control, proven quality"

For organizations with security review, compliance, or procurement requirements.

Everything in Professional, plus:

| Included | Value |
|---|---|
| **Admin console API** | User management (disable/promote), global analytics, failed-document triage & reprocessing |
| **Audit trail** | Every security-relevant action (logins, failed logins, uploads, deletes, visibility changes, admin actions) recorded with actor, resource, IP, timestamp |
| **Full auth lifecycle** | Refresh-token rotation with theft detection, logout revocation, account lockout after repeated failures, password policy, disabled-account enforcement |
| **Hardened attack surface** | Distributed rate limiting on every sensitive endpoint, upload quotas, encrypted-PDF & forged-file rejection, sanitized errors, security headers, request correlation IDs |
| **Proven quality: 108-test automated suite** | 56 unit tests + 52 integration tests against a real database — including the **tenant-isolation regression suite** that fails the build if data ever leaks between users |
| **Graceful degradation** | AI provider outage → keyword-only mode; reranker/cache failures → automatic fallback; honest errors, never fake answers |

**Best for:** security-conscious clients, regulated industries, client-facing deployments.

---

## Why this is different from "a chatbot demo"

| Typical RAG demo | DocuQuery |
|---|---|
| Semantic search only — fumbles exact codes and jargon | **Hybrid retrieval + fusion** — strong on both meaning and exact terms |
| Answers may cite sources that don't exist | **Citation validation & honest refusal** by design |
| One user's documents can leak into another's answers | **Tenant isolation enforced in the retrieval engine**, proven by a dedicated regression suite |
| Quality asserted by the developer | **108-test suite + golden-set benchmark** (85% / 95% / 100% hit@1/3/6) |
| Sends your documents to a third-party API | **Private by default** — fully local AI, provider-agnostic |
| "Works on my machine" | **One-command Docker deploy**, health checks, structured errors, audit trail |

## Use cases

- **Legal & contracts:** "What's the termination notice period in the Acme agreement?"
- **HR & policy:** "How many PTO days do I accrue per month, and do they roll over?"
- **Finance & ops:** "What's the total due on invoice INV-2026-089 and when is it payable?"
- **Research & tender desks:** ask across dozens of dense PDFs and land on the exact page.
- **Scanned archives:** digitized paper becomes as queryable as born-digital files.

## Deployment & operations

- **Deployment:** one command (`docker compose up`) on a single VM — or the same stack
  on Kubernetes/cloud. PostgreSQL, Redis, stateless API, background workers.
- **Private mode:** AI models run on your host. **Cloud mode:** switch to a hosted
  provider with three configuration lines.
- **Operating cost (private mode):** any VM with 8–16 GB RAM; no per-query API spend.
  Cloud mode adds only the provider's token pricing — visible per question in the
  analytics dashboard.
- **Data lifecycle:** hard delete with cascade (documents, chunks, vectors), audit
  trail for compliance review, configurable quotas and retention.

## Engagement model

Editions are delivered as scoped packages with acceptance criteria and automated tests.
Upgrade paths are additive: Starter → Professional → Enterprise, no rework. Roadmap
extensions (office formats, workspaces & roles, citation-highlighting viewer, document
source connectors) are available as further phases.

---

*Technical walkthrough: [ARCHITECTURE.md](ARCHITECTURE.md) · Security: [SECURITY_MODEL.md](SECURITY_MODEL.md) · Quality process: [TESTING_STRATEGY.md](TESTING_STRATEGY.md)*
