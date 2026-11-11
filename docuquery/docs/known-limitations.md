# Release boundary — DEVELOPMENT

The local implementation has substantial automated verification. It is not yet
DEMO READY or a PRODUCTION CANDIDATE under the supplied acceptance plan.

External release gates remain blocked: no configured Supabase project/private
bucket, approved hosted model configuration, published HTTPS URL, or live restore
environment was available. The Render package is prepared; no paid resources were
created and no repository changes were pushed.

- Real-provider factual answers, follow-ups, refusals and adversarial-source behavior
  have not passed the 20-case evaluation. Local browser and integration runs use
  deterministic fake AI, so they prove mechanics, not answer quality.
- Supabase Storage transport has mock tests, and local downloads have ownership
  tests. Actual remote bucket permissions, TLS, role grants, Data API exposure and
  API/worker operation on separate machines still require live verification.
- Worker restart/redelivery, public proxy behavior, sustained load, monitoring,
  backup/restore and rollback need operational rehearsal. Concurrent local sessions
  and retries have passed; they are not a sustained-load benchmark.
- Embeddings must be 768-dimensional for the current migrations. Provider changes
  require compatibility review and reindexing. Embedding outages currently allow
  keyword-only indexing; Ready does not prove that vector generation succeeded.
- Question quota reserves persisted user messages, including failed model attempts.
  Upload quotas reserve document records under an account row lock before storage writes.
- Rate limiting/login lockout can fall back to process-local state when Redis fails.
  Production access-token revocation fails closed. Distributed abuse limits need
  explicit outage testing before production approval.
- Browser access tokens use localStorage. Refresh tokens are HttpOnly cookies.
  Cross-tab refresh coordination uses Web Locks where supported; fallback is
  single-flight within each tab. Multi-tab logout/refresh races need broader testing.
- Physical object cleanup is eventual, after a grace period; monitor failed cleanup.
  Anonymous users cannot download private sources through the API.
- No automated invitation email, password reset, email verification, account expiry,
  SSO, client website widget, billing, or organization workspace is claimed.
- Frontend lint passes in Linux with two existing React optimization warnings.
  Windows native lint is blocked by Application Control. Some test logs include
  deprecation/resource warnings; no skipped test is reported as passing.

Use `upwork-portfolio.md` for a truthful description. Replace this release label only
after the actual deployed gates in `verification-results.md` pass.

## Evidence verification

Answers are now buffered until a second model call checks cited claims. This introduces latency and additional inference cost. Verification uses the configured answer model, so correlated mistakes remain possible; it is not a guarantee of truth. Malformed verification results are refused, and unavailable verification prevents answer publication. Existing historical messages retain their original citations. Three synthetic real-model checks passed (unrelated geography refusal, handbook-only answer, invoice-only answer); broad real-document evaluation remains pending.

## Grounding / retrieval upgrade boundary (11 November 2026)

The phases 1–3 upgrade (soft verification, diagnosed refusals, prompt budget,
embedding prefixes + reindex, neighbor expansion, optional reranker, relevance
floor, degraded ingestion, rich citations) is covered by 295 automated tests,
a mocked-SSE Playwright pass and a live smoke eval against a real 2-page PDF
with qwen2.5:3b. The following remain true and should be re-checked against a
real corpus:

- Soft verification reduces false refusals but does not make the verifier an
  oracle: paraphrases can still be rejected (`verifier_rejected`) and a weak
  model can bless a wrong sentence. `unverified` auto-attributed answers are
  similarity-based, never entailment.
- Changing embed prefixes/contextual settings re-styles every embedder input;
  until `scripts/reindex_embeddings.py` runs, vector recall is depressed (the
  script is resumable and invalidates the answer cache).
- `MIN_RELEVANCE` semantics depend on the ranking source (reranker sigmoid vs
  raw cosine vs ts_rank_cd). The default (0) never refuses previously
  answerable cases; a floor set blindly high can reintroduce refusals.
- `PDF_EXTRACTOR=markdown` has no OCR fallback: scanned pages yield little or
  no text in that mode. Use `plain` for scanned documents.
- The neighbor-expansion context is uncited; quotes verified by the verifier
  may come from either the hit chunk or its attached neighbors — offsets are
  best-effort and fuzzy, so highlights can be a few characters off for
  exotic Unicode.
- The citation "Open page N" action relies on the browser's built-in PDF
  viewer honoring `#page=N` on blob URLs (Chromium/Firefox); other viewers may
  open at page 1.
- The e2e eval measures the configured pipeline only. Small datasets (the
  template's 8 questions) produce noisy rates; fill in 20+ cases from a real
  PDF before comparing profiles.
