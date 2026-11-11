# DocuQuery — Document Q&A with cited answers

**Role:** Python/FastAPI and React full-stack development.

DocuQuery turns uploaded documents into a searchable knowledge base with streaming
Q&A, source excerpts and saved conversations. The implementation includes background
ingestion, OCR support, hybrid PostgreSQL/pgvector retrieval, account isolation,
session renewal, role checks and API permissions.

Engineering work includes retry-safe indexing, authenticated source downloads,
private object-storage support, database migrations, container packaging and automated
regression tests. A Render/Supabase deployment package is prepared.

**Current evidence:** see verification-results.md for exact executed checks. Local
integration tests use deterministic AI substitutes to verify application behavior.
Public deployment, real-provider answer evaluation, and backup restoration are not
yet verified. Describe this as a tested portfolio implementation in development,
not a certified or production-approved service.

Suggested portfolio assets after live acceptance: one Documents screenshot, one
answer with its source excerpt, a four-minute walkthrough, and the architecture
diagram. Label any mocked or recorded demonstration explicitly.

Client message draft (not sent):

“I’ve built DocuQuery, a Python/FastAPI and React document Q&A application with
background ingestion, source excerpts, saved conversations and account isolation.
I can walk you through the implementation and its automated tests. For your MVP,
we can agree on document types, model provider, website integration and acceptance
criteria before finalizing delivery scope.”
