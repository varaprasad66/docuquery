# Ollama setup

From the docuquery directory:

```powershell
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
ollama create docuquery -f ollama/Modelfile.docuquery
```

Set `LLM_MODEL=docuquery` in backend/.env and restart the API. The Modelfile explicitly sets an 8192-token context; `LLM_CONTEXT_BUDGET_TOKENS=6000` limits the estimated input prompt, leaving room for the default 1024-token answer. The estimate is character-based, not exact tokenization.

Keep `GROUNDING_MODE=soft` for the requested forgiving behavior; auto-attribution uses embedding similarity and is labelled unverified. It is not proof that a claim is true. Strict mode rejects incomplete verification. `GROUNDING_AUTO_ATTRIBUTION=False` disables similarity attribution. `LOG_DRAFT_ANSWERS` defaults on locally and off in production when omitted. Drafts are private text stored in the database, capped at 4000 characters; exclude this field from shared diagnostics.

For stronger local hardware, build an equivalent Modelfile with `FROM qwen2.5:14b`. Set `VERIFIER_MODEL` to that model and optionally `REWRITER_MODEL=qwen2.5:3b`. Omitted model overrides follow `LLM_MODEL`. A cloud provider must support the configured chat model, JSON-object response mode and the context budget; Ollama context checking only probes local Ollama endpoints and silently skips unreachable services.

Startup reads `/api/show`. It warns for an explicit `num_ctx` below the budget; when unspecified, it warns that effective context is unknown rather than treating the model's theoretical maximum as runtime context.

## Manual Phase 1 check

1. Run `python -m alembic upgrade head`, then start the API.
2. Upload one PDF and wait for Ready.
3. Ask five questions: direct fact, paraphrase, follow-up, two-part question with one missing part, unrelated question.
4. Inspect the streamed `done` event: `refusal_reason` and `unverified` describe verification, not just document absence.
5. Inspect `SELECT id, refusal_reason, prompt_chars FROM query_logs ORDER BY id DESC LIMIT 5;`. Reasons on partial/unverified answers can be non-ok even when text was returned.
6. Compare soft vs strict; check quotations against the PDF. Test cancellation and separate-account access as before.

Do not publish a lower refusal rate without also measuring unsupported-answer and citation quality. Soft fallback intentionally trades stricter rejection for provisional answers.
