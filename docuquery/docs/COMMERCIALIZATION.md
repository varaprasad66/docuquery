# DocuQuery — Commercialization & Delivery Strategy (INTERNAL)

> **Never share this file or the Implementation Plan with clients.** This is the
> playbook for selling the three editions and delivering them without exposing the
> full product.

---

## 1. The three editions and their cost logic

| Edition | What the client gets | Effort basis | Suggested pricing logic |
|---|---|---|---|
| **Starter** (`EDITION=basic`) | Core RAG: PDF/OCR ingestion, hybrid chat + citations, conversations, accounts + guest demo, tenant isolation, Docker deploy | Already built; delivery is mostly deployment + branding | Fixed price, low four figures. Sell volume here; it's the entry wedge. |
| **Professional** (`EDITION=professional`) | Starter + usage/cost analytics + daily series, semantic cache (cost savings), quotas, feedback loop, search filters, per-user scoping | Built; delivery includes configuring model pricing & quotas to the client's provider | Starter price + 40–70%. Justify with the cache: "it pays for itself on repeated questions." Show the analytics dashboard in the demo. |
| **Enterprise** (`EDITION=enterprise`) | Professional + admin console, audit trail, full auth lifecycle, hardened attack surface, 108-test regression rig | Built; delivery includes a security walkthrough + test report | Professional + 60–120%. Anchor on compliance needs: audit trail, lockout, revocation, and *test-enforced* tenant isolation. This is the tier procurement/security review demands. |

Roadmap phases (office formats, reranker, viewer, workspaces, connectors) are quoted
**per phase on top of any edition** — never bundle them silently.

## 2. How edition gating works (mechanism)

- One codebase. `EDITION` in the environment selects the tier
  (`app/editions.py` + `config.EDITION`).
- Enterprise-only surfaces (`/api/admin/*`, audit) return **404** below Enterprise;
  Professional surfaces (semantic cache, quotas, feedback) are disabled below Professional.
  Lower tiers never see evidence of higher-tier features.
- Security fundamentals are intentionally in **every** edition — never sell isolation
  or rate limiting as an add-on; it protects *your* reputation on every deployment.

## 3. How to deliver without exposing the full product

Choose per client; layers can be combined:

1. **Edition-delivered builds (default).** Set `EDITION` per client and deliver a
   Docker image or a `.zip` of the repo **with the internal docs removed**
   (`docs/IMPLEMENTATION_PLAN.md`, `docs/COMMERCIALIZATION.md`) and a per-client
   `EDITION` pinned in their `.env`. Starter clients literally cannot reach
   Professional/Enterprise surfaces.
2. **Source-strip delivery (paranoid clients or fixed-fee).** Maintain a private
   `main` branch. For delivery, export a stripped copy: remove `tests_integration/`
   harness internals if you don't want to hand over your test rig, keep the
   client-facing `docs/` only. The client can operate and even audit, but not
   reconstruct your full product or your other editions.
3. **Hosted / managed (maximum protection).** You run the stack; the client gets
   accounts and an analytics view. Zero code exposure; recurring revenue. Best for
   Starter-tier clients who don't want ops responsibility.
4. **Demo-driven selling.** Upwork proposals and the gig gallery show only
   [PRODUCT_OVERVIEW.md] content + a recorded demo. Full architecture doc
   ([ARCHITECTURE.md]) is shared **on calls only**, never attached to proposals.

## 4. Contract & hygiene rules

- Every delivery includes a license clause: single-deployment, non-redistribution,
  edition-pinned. No source license unless separately priced (2–3× the build fee).
- Never ship: this file, `IMPLEMENTATION_PLAN.md`, `.env` (secrets), or the golden
  eval dataset (it's part of your quality moat).
- Keep a per-client changelog; upgrades between editions are additive (config change +
  migration), which makes upsells a same-day job — price the upgrade, not a rebuild.
- Before any delivery: run `python scripts/run_tests.py` and include the result summary
  in the handover doc for Enterprise clients.

## 5. Upwork positioning per tier

- **Proposal hook for Starter:** "Ask your PDFs — cited answers with page numbers,
  OCR included, one-command deploy." Attach the Product Overview only.
- **Proposal hook for Professional:** "...plus a cost-optimization layer: repeated
  questions answered from cache at zero token cost, with per-user quotas and a live
  cost dashboard." (This tier matches clients searching for 'RAG with cost control'.)
- **Proposal hook for Enterprise:** "...with test-enforced tenant isolation, an audit
  trail, token revocation, and a 108-test automated suite — built to pass your security
  review." Lead with [TESTING_STRATEGY.md] and [SECURITY_MODEL.md].
- Loom/video walkthroughs beat documents: 3 minutes, Starter demo first, then flash
  the analytics dashboard (Professional) and audit trail (Enterprise) as teasers.
