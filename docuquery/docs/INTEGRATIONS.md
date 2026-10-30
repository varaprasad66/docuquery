# DocuQuery Integrations

Programmatic access to DocuQuery: API keys, webhooks, and ready-made recipes for
no-code automation platforms (n8n, Make).

All endpoints below live on the same origin as the UI, e.g. `http://localhost:8100`.

---

## API keys

Create, list, and revoke keys under `/api/keys` (JWT authentication required —
sign in via `/api/auth/login` and use the access token). The raw key is shown
**exactly once** at creation; only a SHA-256 hash is stored.

```bash
# 1. Sign in (JWT)
curl -s -X POST http://localhost:8100/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "your-password"}'

# 2. Create a key with scopes: read, chat, upload
curl -s -X POST http://localhost:8100/api/keys \
  -H "Authorization: Bearer <ACCESS_TOKEN>" -H "Content-Type: application/json" \
  -d '{"name": "n8n pipeline", "scopes": ["read", "chat", "upload"]}'
# -> {"id": "...", "prefix": "dq_ab12cd34", "key": "dq_...", "scopes": [...], ...}

# 3. Use the key wherever a JWT bearer token is accepted
curl -s http://localhost:8100/api/documents \
  -H "Authorization: Bearer dq_YOUR_RAW_KEY"

# 4. List and revoke
curl -s http://localhost:8100/api/keys -H "Authorization: Bearer <ACCESS_TOKEN>"
curl -s -X DELETE http://localhost:8100/api/keys/<KEY_ID> -H "Authorization: Bearer <ACCESS_TOKEN>"
```

**Scopes**

| Scope | Grants |
|---|---|
| `read` | Search, list/read documents, stats, conversations |
| `chat` | Ask questions (`POST /api/chat`) |
| `upload` | Upload files, import URLs, retry/delete documents |

Revoked or unknown keys are rejected with **401** — a key never silently
downgrades to guest access. Key creation and revocation are audit-logged
(enterprise edition audit trail).

**Creating documents and asking questions with a key**

```bash
# Upload
curl -s -X POST http://localhost:8100/api/documents \
  -H "Authorization: Bearer dq_YOUR_RAW_KEY" \
  -F "file=@/path/report.pdf"

# Ask (SSE stream)
curl -s -N -X POST http://localhost:8100/api/chat \
  -H "Authorization: Bearer dq_YOUR_RAW_KEY" -H "Content-Type: application/json" \
  -d '{"question": "What is the total on invoice INV-2026-089?"}'

# Structured extraction (professional edition)
curl -s -X POST http://localhost:8100/api/extract \
  -H "Authorization: Bearer dq_YOUR_RAW_KEY" -H "Content-Type: application/json" \
  -d '{"document_ids": ["<DOC_ID>"], "template": "invoice"}'
```

---

## Webhooks

Subscribe to document lifecycle events. Events: `document.ready`,
`document.failed`.

```bash
curl -s -X POST http://localhost:8100/api/webhooks \
  -H "Authorization: Bearer <ACCESS_TOKEN>" -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/hooks/docuquery", "events": ["document.ready", "document.failed"]}'
# -> {..., "secret": "whsec_..."}   # shown exactly once
```

- Target URLs must be **public** (private/loopdown/link-local addresses are
  rejected at creation and re-checked at every dispatch).
- Delivery is retried up to `WEBHOOK_MAX_ATTEMPTS` (default 3) with backoff.
- Every attempt series is recorded; inspect via
  `GET /api/webhooks/{id}/deliveries`.

**Verifying signatures** (recommended): each delivery carries

```
X-DocuQuery-Event: document.ready
X-DocuQuery-Signature: t=<unix_seconds>,v1=<hex>
```

where `v1 = HMAC_SHA256(secret, "<t>." + raw_body)`. Reject if the timestamp
drifts more than ~5 minutes. Reference implementation:

```python
import hashlib, hmac, time

def verify(secret: str, body: bytes, header: str, tolerance: int = 300) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    t, v1 = parts["t"], parts["v1"]
    if abs(time.time() - int(t)) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)
```

**Payload shape**

```json
{
  "event": "document.ready",
  "data": {
    "document_id": "…uuid…",
    "document_name": "invoice.pdf",
    "chunk_count": 42
  }
}
```

`document.failed` adds `"error"`.

---

## n8n

1. **HTTP Request node → upload:** method `POST`, URL
   `http://docuquery:8100/api/documents`, authentication *Generic Credential
   Type* → *Header Auth* (`Authorization: Bearer dq_...`) or add a header
   manually. Body type *Form-Data*, field `file` = binary property.
2. **HTTP Request node → ask:** method `POST`, URL `/api/chat`, JSON body
   `{"question": "={{ $json.question }}"}`. The response is an SSE stream — for
   simple automation prefer `/api/search` (plain JSON) or `/api/extract`.
3. **Webhook node:** create a DocuQuery webhook pointing at your n8n Webhook
   node's production URL, then verify `X-DocuQuery-Signature` in a Code node
   with the snippet above before acting on the payload.

## Make (Integromat)

1. **HTTP → Make a request:** method `POST`, URL `.../api/documents`, headers
   `Authorization: Bearer dq_...`, body type *multipart/form-data*, `file` =
   mapped file. Poll `GET /api/documents/{id}` until `status` = `ready`.
2. **Webhooks → Custom webhook:** paste the generated hook URL into
   `POST /api/webhooks`; Make receives `document.ready` pushes automatically.
3. **Router:** branch on `X-DocuQuery-Event` header to handle ready vs failed.
