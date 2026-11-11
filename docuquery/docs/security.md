> Current default: public document sharing is disabled. Ordinary accounts can access only their own documents; guests cannot access documents. Administrators retain privileged access. See the root README for current setup and code map.

# Security boundary

FastAPI validates local signed access tokens, active accounts, ownership and API-key
scopes. Supabase is a database/object store; application JWTs do not populate
Supabase `auth.uid()`. No database tenant RLS guarantee is claimed for direct SQL.
Disable unintended Data API exposure and verify it on the actual project.

Guests can read only explicitly public document listings/searches. Uploads, chat,
conversation history, statistics and source downloads require authentication.
Invalid supplied credentials return 401. Private cross-account resources return
404. API keys cannot administer accounts or delete conversations; document changes
require upload permission. Optional integrations are disabled for the demo.

Refresh cookies are HttpOnly, SameSite=Lax and Secure in production. Refresh rotates
under a database row lock; replay revokes its family. Browsers coordinate refresh
and retry requests at most once. Sign-out revokes server state before clearing the
browser token. Production revocation checks fail closed if Redis is unavailable.
Access tokens remain in localStorage: prevention of script injection remains
important, and a future cookie-only architecture would reduce that exposure.

Administrators are provisioned explicitly by the operator. Registration never grants
admin privilege based on an email address. Account disable is checked on subsequent
authenticated requests. Already-running streams may finish after account disable.

Private object identifiers are generated from owner/document IDs. Source downloads
pass ownership checks and are proxied with no-store headers. Temporary worker copies
are removed after processing. Cleanup failures remain durable and retryable.

Uploaded text is untrusted input to the model. Prompt delimiters are defense in
depth, not proof against prompt injection. Real-provider refusal and citation quality
remain mandatory before a client demonstration. Use only non-sensitive sample data.

Never publish `.env` files, JWT secrets, Supabase privileged keys, provider keys,
uploaded documents, cookies, or database dumps. Both image contexts and Git ignore
private environment files. Debug output and credentials must not enter client guides.
