import type {
  ChatMessage,
  ConversationSummary,
  DocRecord,
  ExtractJob,
  ExtractResult,
  StatsOverview,
  StreamEvent,
  TokenResponse,
  User,
} from "./types";

const BASE = "/api";
const TOKEN_KEY = "docuquery_token";

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {}
}

export function clearStoredToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {}
}

function authHeaders(): Record<string, string> {
  const token = getStoredToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function safeFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    const response = await fetch(url, init);
    const sentToken = new Headers(init?.headers).get("Authorization");
    if (response.status === 401 && sentToken && !url.startsWith(`${BASE}/auth/`)) {
      const current = getStoredToken();
      const renewed = current && sentToken !== `Bearer ${current}` ? current : (await refreshSession())?.access_token;
      if (renewed) {
        const headers = new Headers(init?.headers);
        headers.set("Authorization", `Bearer ${renewed}`);
        return await fetch(url, { ...init, headers });
      }
    }
    return response;
  } catch (err: unknown) {
    if (err instanceof Error && (err.name === "TypeError" || err.message === "Failed to fetch")) {
      throw new Error(
        "Could not connect to DocuQuery. Check your connection and try again.",
      );
    }
    throw err;
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText || `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) {
        detail = body.detail.map((issue: unknown) => {
          if (!issue || typeof issue !== "object") return "Invalid input";
          const error = issue as { loc?: unknown; msg?: unknown };
          const field = Array.isArray(error.loc) ? error.loc.filter((part) => part !== "body").join(".") : "";
          return `${field ? `${field}: ` : ""}${typeof error.msg === "string" ? error.msg.replace(/^Value error, /, "") : "Invalid input"}`;
        }).join("; ");
      }
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// --- Documents ---
export async function fetchDocuments(): Promise<DocRecord[]> {
  return json(await safeFetch(`${BASE}/documents`, { headers: authHeaders() }));
}

export async function uploadDocument(file: File): Promise<DocRecord> {
  const form = new FormData();
  form.append("file", file);
  return json(
    await safeFetch(`${BASE}/documents`, {
      method: "POST",
      headers: authHeaders(),
      body: form,
    }),
  );
}

export async function retryDocument(id: string): Promise<DocRecord> {
  return json(
    await safeFetch(`${BASE}/documents/${id}/retry`, {
      method: "POST",
      headers: authHeaders(),
    }),
  );
}

export async function importDocumentFromUrl(url: string): Promise<DocRecord> {
  return json(
    await safeFetch(`${BASE}/documents/url`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ url }),
    }),
  );
}

// --- Extraction ---
export async function fetchExtractionTemplates(): Promise<Record<string, { description: string; schema: Record<string, unknown> }>> {
  return json(await safeFetch(`${BASE}/extract/templates`, { headers: authHeaders() }));
}

export async function runExtraction(documentIds: string[], template: string): Promise<ExtractResult> {
  return json(
    await safeFetch(`${BASE}/extract`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ document_ids: documentIds, template }),
    }),
  );
}

export async function createExtractionJob(documentIds: string[], template: string): Promise<ExtractJob> {
  return json(
    await safeFetch(`${BASE}/extract/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ document_ids: documentIds, template, async_mode: true }),
    }),
  );
}

export async function fetchExtractionJob(jobId: string): Promise<ExtractJob> {
  return json(await safeFetch(`${BASE}/extract/jobs/${jobId}`, { headers: authHeaders() }));
}

export async function deleteDocument(id: string): Promise<void> {
  const res = await safeFetch(`${BASE}/documents/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error("Delete failed");
}

// --- Health & Stats ---
export async function fetchHealth(): Promise<{
  status: string;
  components: Record<string, string>;
  model: string;
  edition?: string;
  environment?: string;
}> {
  try {
    return await json(await safeFetch(`${BASE}/health`));
  } catch {
    return {
      status: "down",
      components: { database: "down", llm: "down" },
      model: "offline",
    };
  }
}

export async function fetchStats(): Promise<StatsOverview> {
  return json(await safeFetch(`${BASE}/stats`, { headers: authHeaders() }));
}

// --- Auth ---
export async function registerUser(email: string, password: string, displayName?: string): Promise<TokenResponse> {
  const data = await json<TokenResponse>(
    await safeFetch(`${BASE}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, display_name: displayName }),
    }),
  );
  setStoredToken(data.access_token);
  return data;
}

export async function loginUser(email: string, password: string): Promise<TokenResponse> {
  const data = await json<TokenResponse>(
    await safeFetch(`${BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    }),
  );
  setStoredToken(data.access_token);
  return data;
}

export async function fetchMe(): Promise<User | null> {
  const token = getStoredToken();
  if (!token) return (await refreshSession())?.user ?? null;
  try {
    let response = await safeFetch(`${BASE}/auth/me`, { headers: authHeaders() });
    if (response.status === 401) {
      const session = await refreshSession();
      return session?.user ?? null;
    }
    return await json<User>(response);
  } catch {
    clearStoredToken();
    return null;
  }
}

// Session refresh: the refresh token lives in an HttpOnly cookie, so this call
// carries credentials and the CSRF-guard header. Returns null when the session
// cannot be extended (user must sign in again).
let refreshInFlight: Promise<TokenResponse | null> | null = null;
export function refreshSession(): Promise<TokenResponse | null> {
  if (!refreshInFlight) {
    const previous = getStoredToken();
    const renew = async () => {
      const current = getStoredToken();
      if (current && current !== previous) {
        const response = await fetch(`${BASE}/auth/me`, { headers: authHeaders() });
        if (response.ok) return { access_token: current, user: await response.json(), token_type: "bearer" } as TokenResponse;
      }
      return renewSession();
    };
    refreshInFlight = (typeof navigator !== "undefined" && navigator.locks
      ? navigator.locks.request("docuquery-session", renew) : renew()).finally(() => { refreshInFlight = null; });
  }
  return refreshInFlight;
}
async function renewSession(): Promise<TokenResponse | null> {
  try {
    const res = await safeFetch(`${BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
    if (!res.ok) return null;
    const data = (await res.json()) as TokenResponse;
    if (data.access_token) setStoredToken(data.access_token);
    return data;
  } catch {
    return null;
  }
}

export async function logoutUser(): Promise<void> {
  // Finish rotation before revoking its replacement cookie.
  if (refreshInFlight) await refreshInFlight;
  const response = await safeFetch(`${BASE}/auth/logout`, {
    method: "POST", credentials: "include",
    headers: { ...authHeaders(), "X-Requested-With": "XMLHttpRequest" },
  });
  if (!response.ok) throw new Error("Sign-out could not be completed. Please try again.");
  clearStoredToken();
}

// --- Conversations & Chat ---
export async function fetchConversations(): Promise<ConversationSummary[]> {
  return json(await safeFetch(`${BASE}/conversations`, { headers: authHeaders() }));
}

export async function fetchMessages(conversationId: string): Promise<ChatMessage[]> {
  return json(await safeFetch(`${BASE}/conversations/${conversationId}`, { headers: authHeaders() }));
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await safeFetch(`${BASE}/conversations/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error("Delete failed");
}

export async function streamChat(
  question: string,
  conversationId: string | null,
  onEvent: (ev: StreamEvent) => void,
): Promise<void> {
  let res: Response;
  try {
    res = await safeFetch(`${BASE}/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
      },
      body: JSON.stringify({ question, conversation_id: conversationId }),
    });
  } catch (err: unknown) {
    onEvent({
      type: "error",
      error: err instanceof Error ? err.message : String(err),
    });
    return;
  }

  if (!res.ok || !res.body) {
    let detail = `Chat failed (${res.status})`;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {
      /* ignore */
    }
    onEvent({ type: "error", error: detail });
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  let completed = false;
  try {
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const line = block.trim();
      if (!line.startsWith("data: ")) continue;
      try {
        const event = JSON.parse(line.slice(6)) as StreamEvent;
        if (event.type === "done" || event.type === "error") completed = true;
        onEvent(event);
      } catch {
        /* ignore partial frame */
      }
    }
  }
  if (!completed) onEvent({ type: "error", error: "The response was interrupted. Please try again." });
  } catch {
    onEvent({ type: "error", error: "The connection was interrupted. Please try again." });
  } finally {
    reader.releaseLock();
  }
}

export async function fetchConfig(): Promise<{ max_upload_mb: number; url_import_enabled: boolean; registration_enabled: boolean; integrations_enabled: boolean }> {
  return json(await safeFetch(`${BASE}/health/config`));
}
