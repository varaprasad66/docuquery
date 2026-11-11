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
