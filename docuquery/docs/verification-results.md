## Partial-answer and extraction usability follow-up

The generation and evidence prompts now accept supported parts of compound questions and faithful paraphrases. The refusal phrase only suppresses an answer when it is the entire answer, rather than when it appears alongside supported content. Seven grounding unit tests pass. With real local qwen2.5:3b, the user-supplied AI-agent passage produced a cited answer for “what is the ai agents ? how to use them?” and the unrelated largest-country question still produced a refusal with no citations. This check used the supplied passage, not the original PDF or its full retrieval pipeline.

Frontend build passed. A browser test with mocked extraction responses passed for expected fields, missing values, model-confidence labelling, CSV download and stale-result clearing. The extraction model itself was not re-evaluated in that browser test. The current walkthrough is in demo-script.md.

## 2026-11-11 confirmations and evidence checks

- Frontend production build passed. Six Playwright tests passed: one browser confirmation test (mock API, cancel/accept deletion and logout) and five session tests.
- 32 backend tests passed: six evidence-validator unit tests, 12 release regressions and 14 tenant-isolation tests using the disposable PostgreSQL database and fake model transport.
- Real local `qwen2.5:3b` with two synthetic excerpts: unrelated largest-country question refused with zero citations; annual-leave question cited only source 1; invoice-total question cited only source 2. These exercise generation and verification, not full PDF ingestion or a broad quality benchmark.
- Answer text is withheld until evidence verification succeeds. Verifier calls increase latency and usage; semantic verification is fallible. Exact evidence quotation matching is enforced. Existing stored messages are unchanged.
- No public deployment was performed. Public HTTPS, restart/restore and representative hosted-model quality checks remain pending.

## 2026-11-11 account isolation and navigation update

Frontend production build passed. Authorization unit suite: 15 passed. Tenant isolation integration suite: 14 passed against the disposable local database with fake AI. Added strict-default coverage for documents labelled public. Tab preservation compiled successfully; interactive browser verification remains pending for this update. Historical raw logs and superseded planning documents are preserved in the archives ZIP.

# Verification results — 10 November 2026

**Release verdict: DEVELOPMENT.** No public deployment has been observed. Local
verification below does not establish real-provider quality or production approval.

Tested checkout: `zcode/production-hardening`, base commit
`23a6b1539b403f3bffbb3a52335caf83a59b33eb` plus the current uncommitted changes.
Windows host: Python 3.14.8, Node 24.21.0. Production image: Python 3.12/Linux,
Node 24 build stage. Disposable local PostgreSQL 16/pgvector and Redis containers.
Integration/browser AI responses are deterministic fakes. No integration tests ran
against a hosted database. Test cleanup refuses a non-local/non-test database.

## Executed checks

| Check | Result | Evidence / exit status |
|---|---|---|
| Backend unit suite, Windows | PASS | `python ../scripts/run_tests.py unit`: 137 tests, exit 0; unit-latest.log |
| Backend unit suite, Linux image | PASS | `docker run --rm --network none docuquery-render:candidate python -m unittest discover -s tests -v`: 137 tests, exit 0; container-unit.log |
| Full local integration suite | PASS | `python ../scripts/run_tests.py integration`: 101 tests in 80.919s, exit 0; integration-latest.log |
| Final chat/transaction regressions | PASS | `python -m unittest tests_integration.test_release_regressions tests_integration.test_chat_pipeline -v`: 25 tests, exit 0; regressions-latest.log |
| Final ingestion/quota regressions | PASS | `python -m unittest tests_integration.test_release_regressions tests_integration.test_ingestion -v`: 27 tests, exit 0; ingestion-final.log |
| Fresh migration | PASS | `python ../scripts/verify_migrations.py`: empty generated local DB reaches 0006, vector(768), HNSW index and cleanup table verified; exit 0; only generated DB removed |
| Frontend type/build | PASS | `npm run build`, exit 0 |
| Frontend session/stream tests | PASS | `npx playwright test session.spec.ts`: 4 transport-mocked tests, exit 0 |
| Browser workflow | PASS | E2E_BASE_URL=http://127.0.0.1:5174 `npx playwright test smoke.spec.ts`: 1 test, 3.9s, exit 0; registration, upload, ready state, cited answer, 390px layout, logout/reload |
| Container builds | PASS | Backend, Nginx frontend, and combined Render image built locally; container-build.log / render-build.log |
| Frontend lint, Linux | PASS | Image build runs `npm run lint`: zero errors, two React optimization warnings |
| Frontend lint, Windows | BLOCKED | Windows Application Control blocks oxlint native binding; Linux check used above |
| Backend lint | PASS | `ruff check --config pyproject.toml app migrations`, exit 0 |
| Static security scan | PASS | `bandit -c bandit.yaml -r app -q`, exit 0 after fixes; existing narrowly annotated query-template findings reviewed |
| Dependency vulnerability scan | PASS | `python -m pip_audit --progress-spinner off`: no known vulnerabilities; local application package cannot be audited on PyPI; exit 0 |
| Frontend runtime dependency audit | PASS | `npm audit --omit=dev --audit-level=moderate`: zero vulnerabilities, exit 0 |
| Source and production bundle pattern scan | PASS | No matching provider-key/private-key patterns in backend/app, frontend/src or frontend/dist; heuristic, not proof of absence |
| Local Nginx upload/SSE | PASS | 2,098,054-byte synthetic PDF accepted (201), 10 token events plus done; proxy-verification.json; fake AI |
| Whitespace check | PASS | `git diff --check`, exit 0 |
| Remote CI | NOT RUN | Workflow prepared; no push/remote CI invocation |

Counts above overlap: do not add them into a unique-test total. The full 101-test run
preceded the final transaction/upload refinements; their affected suites were rerun
as the 25- and 27-test checks. Three additional regression cases were added afterward
and are included in those focused checks. No tests in the listed passing runs skipped.
Logs include Python deprecation and client resource warnings.

Browser screenshots: `frontend/test-results/chat-desktop.png` and
`frontend/test-results/chat-mobile.png`. They depict mocked AI and must be labelled
as such if shared. Local logs/test-results are ignored by Git; summary and tests are
versioned source artifacts. No recording has been produced.

## Acceptance matrix

“Deployment” refers to the intended live HTTPS/Supabase environment, not localhost.

| ID | Scenario | Local evidence | Deployment |
|---|---|---|---|
| V01 | Empty migrations | PASS: 0001 through 0006 | BLOCKED |
| V02 | Registration/login, no admin escalation | PASS: auth lifecycle; role is always user on registration | BLOCKED |
| V03 | Invalid/expired/revoked credentials | PASS: auth + release regressions | BLOCKED |
| V04 | Logout/reload | PASS: browser smoke and cookie revocation tests | BLOCKED |
| V05 | Expiry/concurrent refresh | PASS: four frontend transport tests plus backend rotation/replay; broader cross-tab testing NOT RUN | BLOCKED |
| V06 | User A/B/guest document matrix | PASS: tenant isolation, private source download checks | BLOCKED |
| V07 | Conversation isolation | PASS: tenant suite; guests require authentication | BLOCKED |
| V08 | Restricted API key | PASS: read keys denied document/conversation mutations; integrations disabled for demo | NOT RUN (disabled target surface) |
| V09 | PDF/TXT ingestion | PASS: ingestion suite, fake embeddings | BLOCKED |
| V10 | Malformed/oversize uploads | PASS: ingestion suite | BLOCKED |
| V11 | Repeated processing | PASS: three repeated jobs; count matches stored chunks | BLOCKED |
| V12 | Concurrency/interruption | PASS: concurrent retries; worker-kill rehearsal NOT RUN | BLOCKED |
| V13 | Cached answer saved | PASS: saved message, citations and ID regression; cache disabled in demo | NOT RUN (disabled target surface) |
| V14 | Delete cached source | PASS: invalidation regression | NOT RUN (cache disabled) |
| V15 | Public-to-private cached source | PASS: revocation regression | NOT RUN (cache disabled) |
| V16 | Private object access | PASS: local ownership + mocked remote transport; real bucket NOT RUN | BLOCKED |
| V17 | Supabase Data API | NOT RUN | BLOCKED |
| V18 | Real factual questions/citations | NOT RUN; fake AI is not evidence | BLOCKED |
| V19 | Follow-up/history | PASS: local mechanics; real-provider behavior NOT RUN | BLOCKED |
| V20 | Unsupported/adversarial questions | PASS: prompt-construction units only; real evaluation NOT RUN | BLOCKED |
| V21 | Provider/stream failure | PASS: sanitized API errors and truncated-stream frontend handling | BLOCKED |
| V22 | Proxy upload and SSE | PASS: local Nginx 2 MB upload and SSE frames | BLOCKED |
| V23 | API/worker redeployment persistence | NOT RUN on intended storage architecture | BLOCKED |
| V24 | URL import SSRF/limits | PASS: optional-feature suite; URL import disabled in demo | NOT RUN (disabled target surface) |
| V25 | Three concurrent sessions | PASS: simultaneous users upload/chat without cross-account citations | BLOCKED |
| V26 | Backup/restore | NOT RUN | BLOCKED |
| V27 | Secrets in source/bundle | PASS: pattern scan and private-file image exclusions; live audit NOT RUN | BLOCKED |
| V28 | Browser usability | PASS: desktop/390px workflow; exhaustive accessibility testing NOT RUN | BLOCKED |

## Failures found and resolved during this work

- Existing partial password edits referred to undefined EmailStr; corrected validation.
- Old tests expected shared guest uploads/stats; changed behavior and assertions to
  authenticated private flows, retaining cross-account regression coverage.
- Windows restricted async test runs stalled; normal local execution passed.
- Browser server selected an incompatible Proactor loop with current Uvicorn/Python;
  explicit SelectorEventLoop now used by local entrypoints.
- Browser Ready locator anchored before leading whitespace; exact text locator fixed.
- Early simultaneous browser reruns conflicted over trace output; the final passing
  run was isolated. Earlier failed runs are not counted as passes.
- Source scan identified low-level silent exception handling; logging and production
  fail-closed revocation were added, then static checks rerun.

See known-limitations.md and deployment.md for the required next release actions.

## Final package identity

Final local Render image: `sha256:6ffecb78189ce97c8ec16ba5a139a3d1b1677d16847a00ba7c962d578d1c3a56`.
Final image import/content check PASS: app imports, built frontend and account command
are present, `.env` is absent. `docker compose -f compose.production.yml config
--no-env-resolution --quiet` exits 0. Render YAML parses with four services, automatic
deploy disabled and no public queue allowlist. Render server-side Blueprint validation
and provisioning have NOT RUN. Runtime dependency pins include the resolved Linux
image inventory (`backend/constraints-linux.txt`) merged into `constraints.txt`.
