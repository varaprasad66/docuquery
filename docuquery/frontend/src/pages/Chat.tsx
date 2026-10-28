import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteConversation,
  fetchConversations,
  fetchMessages,
  streamChat,
} from "../api";
import type { ChatMessage, Citation, ConversationSummary } from "../types";

interface LocalMessage extends ChatMessage {
  streaming?: boolean;
}

function AnswerBody({ content }: { content: string }) {
  // Render [1] style markers as clickable-looking superscript chips
  const parts = content.split(/(\[\d+\])/g);
  return (
    <p className="text-sm leading-relaxed whitespace-pre-wrap">
      {parts.map((part, i) =>
        /^\[\d+\]$/.test(part) ? (
          <sup
            key={i}
            className="ml-0.5 px-1.5 py-0.5 bg-indigo-50 text-indigo-700 rounded text-[10px] font-semibold"
          >
            {part.slice(1, -1)}
          </sup>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </p>
  );
}

function CitationCard({ c }: { c: Citation }) {
  return (
    <div className="border border-slate-200 rounded-lg p-3 bg-white">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="w-5 h-5 grid place-items-center rounded bg-indigo-600 text-white text-[10px] font-bold">
          {c.n}
        </span>
        <span className="text-xs font-medium text-slate-700 truncate">{c.document_name}</span>
        <span className="text-[10px] text-slate-400">page {c.page}</span>
      </div>
      <p className="text-xs text-slate-500 leading-relaxed line-clamp-4">{c.snippet}</p>
    </div>
  );
}

export default function ChatPage() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  // While streaming into a conversation, switching activeId must not trigger
  // the history refetch (it would wipe the in-flight assistant bubble).
  const skipHistoryFetch = useRef(false);

  const loadConversations = useCallback(() => {
    fetchConversations()
      .then(setConversations)
      .catch(() => setConversations([]));
  }, []);

  useEffect(() => loadConversations(), [loadConversations]);

  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      return;
    }
    if (skipHistoryFetch.current) {
      skipHistoryFetch.current = false;
      return;
    }
    fetchMessages(activeId)
      .then(setMessages)
      .catch(() => setMessages([]));
  }, [activeId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const ask = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q || busy) return;
      setBusy(true);
      setInput("");

      setMessages((prev) => [
        ...prev,
        {
          id: `local-${Date.now()}`,
          role: "user",
          content: q,
          citations: null,
          created_at: new Date().toISOString(),
        },
      ]);

      const assistantId = `stream-${Date.now()}`;
      let newConversationId: string | null = activeId;

      await streamChat(q, activeId, (ev) => {
        if (ev.type === "meta") {
          newConversationId = ev.conversation_id;
          skipHistoryFetch.current = true;
          setActiveId(ev.conversation_id);
          setMessages((prev) => [
            ...prev,
            {
              id: assistantId,
              role: "assistant",
              content: "",
              citations: [],
              streaming: true,
              created_at: new Date().toISOString(),
            },
          ]);
        } else if (ev.type === "citations") {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, citations: ev.citations } : m)),
          );
        } else if (ev.type === "token") {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + ev.value } : m)),
          );
        } else if (ev.type === "done") {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, streaming: false, latency_ms: ev.latency_ms }
                : m,
            ),
          );
        } else if (ev.type === "error") {
          setMessages((prev) => [
            ...prev.filter((m) => m.id !== assistantId),
            {
              id: assistantId,
              role: "assistant",
              content: `⚠ ${ev.error}`,
              citations: null,
              created_at: new Date().toISOString(),
            },
          ]);
        }
      });

      setBusy(false);
      if (newConversationId !== activeId) loadConversations();
    },
    [activeId, busy, loadConversations],
  );

  const removeConversation = async (id: string) => {
    await deleteConversation(id).catch(() => undefined);
    if (activeId === id) {
      setActiveId(null);
      setMessages([]);
    }
    loadConversations();
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-6 grid grid-cols-[240px_1fr] gap-6 h-[calc(100vh-73px)]">
      {/* Conversations sidebar */}
      <aside className="flex flex-col min-h-0">
        <button
          onClick={() => {
            setActiveId(null);
            setMessages([]);
          }}
          className="w-full mb-3 px-3 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 transition-colors"
        >
          + New conversation
        </button>
        <div className="flex-1 overflow-y-auto space-y-1">
          {conversations.map((c) => (
            <div
              key={c.id}
              className={`group flex items-center gap-1 px-3 py-2 rounded-lg cursor-pointer text-sm transition-colors ${
                activeId === c.id
                  ? "bg-indigo-50 text-indigo-800"
                  : "hover:bg-slate-100 text-slate-700"
              }`}
              onClick={() => setActiveId(c.id)}
            >
              <span className="flex-1 truncate">{c.title}</span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  removeConversation(c.id);
                }}
                className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-rose-600"
                title="Delete conversation"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M3 6h18M8 6V4h8v2m-9 0v14a2 2 0 002 2h6a2 2 0 002-2V6" />
                </svg>
              </button>
            </div>
          ))}
        </div>
      </aside>

      {/* Chat pane */}
      <section className="flex flex-col min-h-0 bg-white border border-slate-200 rounded-xl">
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {messages.length === 0 && (
            <div className="h-full grid place-items-center text-center">
              <div>
                <p className="text-slate-400 text-sm mb-3">
                  Ask anything about your uploaded documents.
                </p>
                <div className="flex flex-wrap gap-2 justify-center">
                  {[
                    "What is our vacation policy?",
                    "Summarize the payment terms in the invoice",
                    "Who approves remote work arrangements?",
                  ].map((s) => (
                    <button
                      key={s}
                      onClick={() => ask(s)}
                      className="text-xs px-3 py-1.5 rounded-full border border-slate-200 text-slate-600 hover:border-indigo-300 hover:text-indigo-700 transition-colors"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[75%] bg-indigo-600 text-white text-sm px-4 py-2.5 rounded-2xl rounded-br-md">
                  {m.content}
                </div>
              </div>
            ) : (
              <div key={m.id} className="flex flex-col gap-2 max-w-[85%]">
                <div className="bg-slate-50 border border-slate-100 px-4 py-3 rounded-2xl rounded-bl-md">
                  <AnswerBody content={m.content || "…"} />
                  {m.streaming && (
                    <span className="inline-block w-1.5 h-4 bg-indigo-400 animate-pulse ml-0.5 align-middle" />
                  )}
                  {!m.streaming && m.latency_ms ? (
                    <p className="text-[10px] text-slate-400 mt-2">
                      {(m.latency_ms / 1000).toFixed(1)}s
                    </p>
                  ) : null}
                </div>
                {m.citations && m.citations.length > 0 && (
                  <div className="grid gap-2 sm:grid-cols-2">
                    {m.citations.map((c) => (
                      <CitationCard key={c.n} c={c} />
                    ))}
                  </div>
                )}
              </div>
            ),
          )}
          <div ref={bottom} />
        </div>

        <div className="border-t border-slate-100 p-3">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              ask(input);
            }}
            className="flex gap-2"
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about your documents…"
              disabled={busy}
              className="flex-1 px-4 py-2.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 disabled:bg-slate-50"
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              className="px-5 py-2.5 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-40 transition-colors"
            >
              {busy ? "Thinking…" : "Send"}
            </button>
          </form>
        </div>
      </section>
    </div>
  );
}
