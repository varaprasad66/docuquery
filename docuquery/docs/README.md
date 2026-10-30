# DocuQuery — Documentation

DocuQuery is a production-grade, privacy-first document intelligence platform: upload
documents (including scanned PDFs), and chat across them with hybrid retrieval, streaming
answers, and page-level citations.

This documentation set has two audiences:

**Client-facing (safe to share — no code, no internals):**

| Document | Purpose |
|---|---|
| [Product Overview & Editions](PRODUCT_OVERVIEW.md) | One-pager: what the product does and the three sellable editions (Starter / Professional / Enterprise). Send this first. |
| [Architecture & RAG Workflow](ARCHITECTURE.md) | How the system works end-to-end: ingestion pipeline, hybrid retrieval, cited answer generation, deployment topology, edition matrix. Walk clients through this on a call. |
| [Security Model](SECURITY_MODEL.md) | Authentication, tenant isolation, data protection, rate limiting, auditability. For security-conscious clients. |
| [Testing & Quality Strategy](TESTING_STRATEGY.md) | The implemented four-layer test rig (108 automated tests), quality gates, and benchmark methodology. |

**Internal (never ship to clients):**

| Document | Purpose |
|---|---|
| [Runbook](RUNBOOK.md) | How to run the stack, run all three verification layers, and demo the tiers. |
| [Implementation Plan](IMPLEMENTATION_PLAN.md) | The full phased roadmap — every feature, edge case, security control, and test per phase. |
| [Commercialization & Delivery](COMMERCIALIZATION.md) | How to package, price, and deliver the three editions to clients without exposing the full product. |

## Status snapshot

- **Shipped (v0.3, Enterprise edition):** PDF ingestion with OCR fallback, hybrid
  retrieval with tenant-scoped queries, streaming chat with page-level citations,
  semantic answer cache (cost optimization), usage/cost analytics with daily series,
  answer feedback loop, refresh-token auth lifecycle with revocation & lockout,
  distributed rate limiting, admin console API, audit trail, security hardening
  (headers, request IDs, sanitized errors, quotas), Docker deployment.
- **Proven:** 108 automated tests green — 56 unit + 52 integration against a real
  database, including the tenant-isolation regression suite.
- **Roadmap (quote as phases):** office-format ingestion, reranker, citation-highlighting
  viewer, workspaces & RBAC, CI/CD tail (eval gate), connectors (Drive/SharePoint/S3).
  See the [Implementation Plan](IMPLEMENTATION_PLAN.md).
