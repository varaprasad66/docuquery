# DocuQuery portfolio video: presenter script

Target: 6–7 minutes. Record at 1080p with readable text, clear voice and no background music over narration. This is a demonstration project, not a client success story. Use only synthetic or publishable documents and hide credentials, personal emails and browser notifications. Edit waiting periods out and label time cuts; do not simulate successful responses.

## Prepare before recording

- Start Docker database/Redis, Ollama, backend and frontend using the README.
- Restart the backend after code changes and use a new conversation for the corrected evidence policy.
- Prepare a handbook with known leave rules, an invoice with known amounts, a sample contract, and your AI-agent paper. Include a scanned PDF only if OCR is installed and you have verified that sample.
- Use two separate demo accounts. Account B must have no access to account A's documents.
- Rehearse each exact question. Check cited quotations against the originals. Do not promise that every response will match this script word for word.
- Extract and Analytics must be enabled in the deployed/local configuration. If a feature is disabled, omit its demonstration or enable and test it before recording.
- Do not film secrets in `.env`, API keys, tokens or Docker configuration output.

## 0:00–0:20 — Problem and product

Screen: clean Documents page, with your name/title as a simple video overlay.

Say: “This is DocuQuery, a document question-answering application I built with React, FastAPI and PostgreSQL. It helps users find answers in their own documents, review the supporting passages, and turn invoices or contracts into structured data.”

## 0:20–0:50 — Accounts and uploading

Screen: sign in to demo account A. Upload the handbook, invoice and AI-agent paper. Show Processing and Ready, cutting the wait if needed.

Say: “Each user has an individual account. I can upload documents, and the application extracts text, splits it into searchable passages and indexes it. Supported formats include PDF and common Office and text formats. Scanned PDFs use OCR when available.”

Optional insert: show one tested scanned PDF and its extracted answer. Do not claim OCR verification if you did not run it.

## 0:50–1:35 — Document question and useful citations

Screen: open Chat. Ask a known handbook question, such as “How many annual leave days are provided?” Open the cited source and compare the evidence quotation with the handbook.

Say: “Instead of searching page by page, I ask a natural-language question. The answer cites the supporting excerpt. The source card shows the document, location and evidence quotation so I can verify the response myself.”

Then ask a known invoice question.

Say: “This question is about the invoice. The answer should cite the invoice alone; the handbook is not automatically added just because it was also retrieved.”

## 1:35–2:15 — Partial answers and refusing unrelated questions

Screen: new conversation. Ask “What are AI agents, and how do I use them?” using your paper. Show the supported definition and any explicit limit about missing practical instructions.

Say: “When a document supports only part of a question, the assistant should still give that useful part with citations. It should also explain which details are missing, instead of rejecting the whole question.”

Ask “Which country is the largest in the world?” when none of the uploaded documents answers it.

Say: “This is a document-grounded assistant. Even a fact the model knows should not be presented as an answer from an unrelated PDF. Here it declines and attaches no misleading citations. These checks reduce unsupported answers; they are not a guarantee of perfect accuracy.”

## 2:15–2:45 — Conversations and navigation

Screen: ask a supported follow-up, switch between Documents and Chat, reload, then reopen saved conversation history. Optionally use Copy and answer feedback if visible.

Say: “Conversations are saved to the account. I can return to previous questions and supporting sources. Switching tabs preserves the current page state, while uploads still update during processing.”

## 2:45–3:45 — Structured extraction

Screen: open Extract. Select the invoice and Invoice template. Point to the expected fields, run extraction and review the results against the original. Export CSV and JSON. Then briefly show the Contract template with a matching document.

Say: “Chat is useful for individual questions. Extract is useful when I need the same fields repeatedly. For an invoice, it returns fields such as invoice number, vendor, dates, currency, total and line items. A contract uses a different schema. Missing information is shown explicitly rather than filled in by the interface.”

Say: “I review the values and their source locations before exporting. CSV can be used in spreadsheets, while JSON can be consumed by another application. The displayed confidence comes from the model and is not a verified accuracy score.”

## 3:45–4:10 — Analytics

Screen: open Analytics after generating a few questions. Describe only metrics actually shown.

Say: “The analytics view helps inspect usage, response time and token consumption. Cost estimates depend on the configured provider prices; local-model usage can show zero API cost. This helps explain the operating tradeoffs of a document assistant.”

## 4:10–5:00 — Confirmation and account isolation

Screen: click Delete on a disposable document, then Cancel. Click Sign Out, Cancel once, then confirm. Show the signed-out page with no documents. Sign in as account B and show its separate list/history.

Say: “Destructive actions have confirmations. Cancelling deletion sends no delete request. Signing out removes the account's workspace from the screen. A different account does not inherit these documents or conversations. Authorization is also enforced by the API, rather than relying only on hiding buttons.”

Do not delete your main showcase documents during the video. If showing confirmed deletion, use a separate disposable sample.

## 5:00–5:45 — Engineering and evidence

Screen: README architecture diagram and code map, then a concise test-results summary without secrets.

Say: “The frontend is React and TypeScript. FastAPI handles authentication and document APIs. PostgreSQL and pgvector support full-text and semantic retrieval. The application supports background ingestion, and production deployment configuration includes workers and private storage. The README explains how to run the project and where each responsibility lives.”

Say: “Automated checks cover account isolation, session behavior, confirmations and evidence validation. I also test the actual model with both supported and unrelated questions. Hosted deployment, recovery and broader document-quality checks are separate acceptance steps.”

## 5:45–6:10 — Close with the service you offer

Screen: clean application view and an end card: “Document AI · RAG · FastAPI · React”.

Say: “This project demonstrates the document workflows I can adapt for a business: searchable internal knowledge, cited document answers, and structured invoice or contract extraction. A client implementation would use their document types, access rules, integrations and acceptance tests.”

Use Upwork's own proposal or profile contact flow. Do not promise zero hallucinations, guaranteed cost savings, unlimited scale or a production deployment that has not been verified.

## Optional technical appendix (not required in the main video)

API keys, webhooks, administrative endpoints, extraction job queues, rate limits and worker recovery are backend capabilities. Demonstrate each only with an enabled, rehearsed endpoint and redacted credentials. They are not all buttons in the current UI. URL import and answer caching may be disabled by configuration. Avoid presenting disabled capabilities as an available hosted feature.

For a short portfolio preview, cut a 60–90 second version showing upload, one cited answer, the unrelated-question refusal, extraction export and account separation. Link the longer walkthrough and repository where permitted.
