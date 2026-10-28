import type {
  ChatMessage,
  ConversationSummary,
  DocRecord,
  StreamEvent,
} from "./types";

const BASE = "/api";

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

export async function fetchDocuments(): Promise<DocRecord[]> {
  return json(await fetch(`${BASE}/documents`));
}

export async function uploadDocument(file: File): Promise<DocRecord> {
  const form = new FormData();
  form.append("file", file);
  return json(await fetch(`${BASE}/documents`, { method: "POST", body: form }));
}

export async function deleteDocument(id: string): Promise<void> {
  const res = await fetch(`${BASE}/documents/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Delete failed");
}

export async function fetchHealth(): Promise<{
  status: string;
  components: Record<string, string>;
  model: string;
}> {
  return json(await fetch(`${BASE}/health`));
}

export async function fetchConversations(): Promise<ConversationSummary[]> {
  return json(await fetch(`${BASE}/conversations`));
}

export async function fetchMessages(conversationId: string): Promise<ChatMessage[]> {
  return json(await fetch(`${BASE}/conversations/${conversationId}`));
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await fetch(`${BASE}/conversations/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Delete failed");
}

/**
 * POST /api/chat and consume the SSE stream.
 * EventSource only supports GET, so we parse the stream manually.
 */
export async function streamChat(
  question: string,
  conversationId: string | null,
  onEvent: (ev: StreamEvent) => void,
): Promise<void> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, conversation_id: conversationId }),
  });
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
        /* skip malformed block */
      }
    }
  }
}
