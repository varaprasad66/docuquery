import type {
  ChatMessage,
  ConversationSummary,
  DocRecord,
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
    return await fetch(url, init);
  } catch (err: unknown) {
    if (err instanceof Error && (err.name === "TypeError" || err.message === "Failed to fetch")) {
      throw new Error(
        "Could not connect to backend server at http://127.0.0.1:8100. Please ensure the backend is running (python run.py).",
      );
    }
    throw err;
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
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
  if (!token) return null;
  try {
    return await json<User>(
      await safeFetch(`${BASE}/auth/me`, { headers: authHeaders() }),
    );
  } catch {
    clearStoredToken();
    return null;
  }
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
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    onEvent({ type: "error", error: detail });
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

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
        onEvent(JSON.parse(line.slice(6)) as StreamEvent);
      } catch {
        /* ignore partial frame */
      }
    }
  }
}
