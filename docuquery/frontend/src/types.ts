export type DocumentStatus = "processing" | "ready" | "failed";

export interface DocRecord {
  id: string;
  original_name: string;
  status: DocumentStatus;
  error: string | null;
  page_count: number;
  chunk_count: number;
  progress: number;
  size_bytes: number;
  created_at: string;
}

export interface Citation {
  n: number;
  document_id: string;
  document_name: string;
  page: number;
  snippet: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[] | null;
  latency_ms?: number;
  created_at: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  message_count: number;
}

export type StreamEvent =
  | { type: "meta"; conversation_id: string; retrieval_ms: number; methods: string[] }
  | { type: "citations"; citations: Citation[] }
  | { type: "token"; value: string }
  | { type: "done"; message_id: string; prompt_tokens: number; completion_tokens: number; latency_ms: number }
  | { type: "error"; error: string };
