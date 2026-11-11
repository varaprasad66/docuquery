# Four-minute walkthrough and access guide

Rehearsal script; live deployment and real-model acceptance are still pending.

0:00 — Sign in with an individual restricted account. Explain that uploads and
conversation history are isolated. Use synthetic documents only.

0:30 — Upload the sample handbook. Show processing advancing to Ready. Explain
that text is split into searchable passages in a background worker.

1:15 — Ask “How many annual leave days are available?” Open the citation and compare
its excerpt with the handbook. Do not treat a citation number alone as evidence.

2:00 — Ask a follow-up about carryover. Then ask an unsupported question about an
unlisted employee's salary. Show the real model's refusal, if it passes evaluation.

2:45 — Reload and reopen the conversation. Demonstrate saved messages and sources.
Sign out and reload to show that the account remains signed out.

3:30 — Explain React, FastAPI, background ingestion, PostgreSQL/pgvector and private
storage. State observed limits and discuss the client's website integration needs.

Access guide: open the verified HTTPS URL supplied privately by the owner, sign in,
open Documents, upload a small PDF or TXT, wait for Ready, then open Chat. The proposed
demo limit is 10 MB per document and 20 questions per day. Do not upload personal or
confidential material. Contact the owner for upload errors or access changes.
The owner disables access on the agreed review date; automatic expiry is not claimed.

No live URL is listed because none has been deployed and verified in this work.
