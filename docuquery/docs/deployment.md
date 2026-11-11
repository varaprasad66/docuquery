# Deployment runbook

Status: prepared locally; no public deployment or paid resources created.
Recommended target: Render Docker web service, Celery worker, one Celery scheduler,
private Render Key Value queue, and a separate Supabase project in a nearby region.
The reviewed specification is at https://render.com/docs/blueprint-spec.

## Before provisioning

Review `render.yaml` at the repository root. The selected managed services incur charges; approve
the dashboard's current total before creating them. Model usage and Supabase are
separate. No cost estimate or availability guarantee has been verified here.
Use non-sensitive sample documents. The current release remains DEVELOPMENT until
the deployed verification matrix passes.

## Supabase preparation

1. Create a separate demo project. Disable its Data API if unused. This application
   uses FastAPI JWT identities and direct SQL, not Supabase Auth.
2. Enable pgvector. Ensure the SQL search path includes the installed extension
   schema (`public, extensions` as applicable). Migrations currently require 768
   dimensional embeddings; do not merely change EMBED_DIM for another model.
3. Create a PRIVATE `documents` bucket with no public read policy. Set a server-only
   secret key. Never use a `VITE_*` variable for it.
4. Set separate runtime and migration credentials. Runtime requires SELECT/INSERT/
   UPDATE/DELETE on application tables and sequence usage, not schema ownership,
   CREATE ROLE, or BYPASSRLS. Grant only the explicit application tables, not every
   Supabase schema. Migration credentials own application DDL.
5. Use direct connections or the session pooler on port 5432. The worker uses
   session advisory locks: transaction pooling is NOT supported. Require TLS;
   prefer `sslmode=verify-full` with the project's CA where supported. Verify the
   actual API and worker sessions with `pg_stat_ssl` after deployment.
6. With a publishable/anonymous credential, explicitly verify application table
   reads fail. Also test private object download with no key and another user.

Connection reference: https://supabase.com/docs/guides/database/connecting-to-postgres
Private objects: https://supabase.com/docs/guides/storage/serving/downloads

## Render setup

Create an environment group named `docuquery-secrets` with DATABASE_URL, JWT_SECRET,
CORS_ORIGINS (the exact HTTPS origin), SUPABASE_URL, SUPABASE_SECRET_KEY,
SUPABASE_STORAGE_BUCKET, LLM_BASE_URL, LLM_API_KEY, LLM_MODEL,
EMBEDDINGS_BASE_URL, EMBEDDINGS_API_KEY, EMBEDDINGS_MODEL, and EMBED_DIM=768.
Use dashboard secret fields, never source files or chat messages.

Add MIGRATION_DATABASE_URL only to the web service, whose pre-deploy step runs
Alembic once. Keep it out of the shared group and worker/scheduler environments.
The runtime web process still receives it on Render; a separate privileged release
job is preferable for stricter separation. The application does not use it for
requests. Configure the value before the first deployment.

Import the reviewed Blueprint. It serves the built React app and `/api` together
from the web container, behind Render HTTPS. No CORS-dependent second browser data
API is needed. The queue accepts only private-network connections. Do not scale the
scheduler beyond one replica. Auto deploy is off: release deliberately after CI.

Pool ceiling: web 4 connections; each of two ingestion worker processes up to 4;
scheduler itself dispatches tasks without a database connection. Reserve additional
capacity for migrations, monitoring, and jobs. Actual concurrency must be measured.

## Initial release

Build `docuquery/Dockerfile.render`; run migrations before starting new workers.
Check `/api/health/live` and `/api/health/ready`. Readiness covers the database and
queue; it does NOT prove AI quality or worker progress. Monitor ready/failed/stuck
documents, queue depth, worker heartbeats, provider errors, and request latency.

Create an individual restricted account from a backend shell:

```
python manage_account.py create client@example.com
```

The Render image includes this script. The standalone backend Docker image contains backend files only: copy the reviewed operator
script into a private operational checkout or run it there with the same environment.
Passwords are entered without echo. Deliver credentials privately, separately from
portfolio material. Registration and optional integrations are disabled in the
production manifests. Disable the account on the agreed date with the script's
`disable` action. Automatic account expiry is not implemented.

Rehearse upload, processing, chat, citation, reload, logout, two-account isolation,
three concurrent sessions, and permitted uploads above 1 MB through the real URL.
Run the 20-case evaluation in `demo-evaluation.md` with actual providers.

## Recovery and maintenance

The scheduler retries processing documents whose progress has not changed for five
minutes. Advisory locks prevent overlapping ingestion; chunk replacement is atomic.
Broker failure leaves a recoverable processing record instead of using in-process
work in production. Inspect repeatedly failing jobs rather than retrying forever.

Storage writes have a durable compensation record. Document deletion atomically
queues object deletion; private bytes may remain until maintenance succeeds.
Cleanup retries every five minutes after a one-hour grace period. Monitor the
`storage_cleanup` backlog. Local development can invoke `cleanup_storage()` manually.
Do not advertise immediate physical erasure; API access is revoked immediately.

Back up PostgreSQL and private objects separately. Record object key, checksum,
document ID, and owner ID in a manifest. Restore both into a NEW isolated project;
verify counts, chunks, source checksums, ownership and conversation history before
claiming restore readiness. Never run integration-test cleanup on hosted databases.

Rollback: retain the prior image digest and configuration. Stop ingestion, deploy
the previous compatible image, check readiness and smoke tests, then resume workers.
Keep additive migration 0006 in place; do not downgrade data-bearing schemas blindly.
Restore from tested backups if schema compatibility prevents an application rollback.

Alternative container-host deployment: `compose.production.yml` uses the same
settings from a private `.env.production`, binds only the frontend to loopback, and
expects a host HTTPS reverse proxy. Run its `migrate` release profile once before
starting application services. Nginx permits 51 MB request bodies; production API
limits document payloads to 10 MB, leaving multipart overhead at the proxy.
