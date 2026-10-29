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
  owner_id?: string | null;
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

export interface User {
  id: string;
  email: string;
  display_name: string | null;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface LatencyStats {
  avg_total_ms: number;
  avg_retrieval_ms: number;
  avg_llm_ms: number;
}

export interface TokenStats {
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
}

export interface QueryLog {
  id: number | string;
  question: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  retrieval_ms: number;
  llm_ms: number;
  total_ms: number;
  created_at: string;
}

export interface StatsOverview {
  total_documents: number;
  ready_documents: number;
  failed_documents: number;
  total_chunks: number;
  total_pages: number;
  total_queries: number;
  latency: LatencyStats;
  tokens: TokenStats;
  recent_queries: QueryLog[];
}
