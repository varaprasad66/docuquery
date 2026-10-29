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
  retrieval_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
}

function AnswerBody({
  content,
  onCitationClick,
}: {
  content: string;
  onCitationClick: (n: number) => void;
}) {
  const parts = content.split(/(\[\d+\])/g);
  return (
    <div className="text-sm leading-relaxed whitespace-pre-wrap text-slate-800">
      {parts.map((part, i) => {
        if (/^\[\d+\]$/.test(part)) {
          const num = parseInt(part.slice(1, -1), 10);
          return (
            <button
              key={i}
              onClick={() => onCitationClick(num)}
              className="ml-0.5 inline-flex items-center justify-center px-1.5 py-0.2 bg-indigo-100 hover:bg-indigo-200 text-indigo-800 rounded text-[11px] font-bold cursor-pointer transition-colors"
              title={`View Citation [${num}]`}
            >
              {num}
            </button>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </div>
  );
}

function CitationCard({
  c,
  onClick,
}: {
  c: Citation;
  onClick: () => void;
}) {
  return (
    <div
      onClick={onClick}
      className="border border-slate-200 hover:border-indigo-300 rounded-lg p-3 bg-white hover:bg-indigo-50/20 cursor-pointer transition-all shadow-xs"
    >
      <div className="flex items-center gap-2 mb-1.5">
        <span className="w-5 h-5 grid place-items-center rounded bg-indigo-600 text-white text-[10px] font-bold shrink-0">
          {c.n}
        </span>
        <span className="text-xs font-semibold text-slate-800 truncate flex-1">{c.document_name}</span>
        <span className="text-[10px] font-medium text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">
          Page {c.page}
        </span>
      </div>
      <p className="text-xs text-slate-600 leading-relaxed line-clamp-3 font-normal">{c.snippet}</p>
    </div>
  );
}

export default function ChatPage() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const bottom = useRef<HTMLDivElement>(null);
  const skipHistoryFetch = useRef(false);

  const loadConversations = useCallback(() => {
    fetchConversations()
      .then(setConversations)
      .catch(() => setConversations([]));
  }, []);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

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

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

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
              retrieval_ms: ev.retrieval_ms,
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
                ? {
                    ...m,
                    streaming: false,
                    latency_ms: ev.latency_ms,
                    prompt_tokens: ev.prompt_tokens,
                    completion_tokens: ev.completion_tokens,
                  }
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

  const handleCitationClick = (citationNum: number, currentMsgCitations: Citation[] | null) => {
    if (!currentMsgCitations) return;
    const match = currentMsgCitations.find((c) => c.n === citationNum);
    if (match) setSelectedCitation(match);
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-6 grid grid-cols-[250px_1fr] gap-6 h-[calc(100vh-73px)]">
      {/* Sidebar */}
      <aside className="flex flex-col min-h-0 bg-white border border-slate-200 rounded-xl p-3 shadow-xs">
        <button
          onClick={() => {
            setActiveId(null);
            setMessages([]);
          }}
          className="w-full mb-3 px-3 py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold flex items-center justify-center gap-2 transition-colors shadow-xs"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          New conversation
        </button>

        <div className="text-[11px] font-bold text-slate-400 uppercase tracking-wider px-2 mb-2">
          History ({conversations.length})
        </div>

        <div className="flex-1 overflow-y-auto space-y-1 pr-1">
          {conversations.length === 0 && (
            <p className="text-xs text-slate-400 text-center py-6">No previous conversations.</p>
          )}
          {conversations.map((c) => (
            <div
              key={c.id}
              className={`group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer text-xs font-medium transition-colors ${
                activeId === c.id
                  ? "bg-indigo-50 text-indigo-800 font-semibold"
                  : "hover:bg-slate-100 text-slate-700"
              }`}
              onClick={() => setActiveId(c.id)}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-slate-400 shrink-0">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
              <span className="flex-1 truncate">{c.title}</span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  removeConversation(c.id);
                }}
                className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-rose-600 transition-opacity p-0.5"
                title="Delete"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M3 6h18M8 6V4h8v2m-9 0v14a2 2 0 002 2h6a2 2 0 002-2V6" />
                </svg>
              </button>
            </div>
          ))}
        </div>
      </aside>

      {/* Main Chat Area */}
      <section className="flex flex-col min-h-0 bg-white border border-slate-200 rounded-xl shadow-xs overflow-hidden">
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
          {messages.length === 0 && (
            <div className="h-full grid place-items-center text-center py-12">
              <div className="max-w-md">
                <div className="w-12 h-12 rounded-2xl bg-indigo-50 text-indigo-600 grid place-items-center mx-auto mb-3">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
                  </svg>
                </div>
                <h3 className="font-semibold text-slate-900 mb-1">DocuQuery Intelligence</h3>
                <p className="text-slate-500 text-xs mb-5">
                  Ask multi-document questions. Answers include verifiable page citations and reciprocal rank fusion excerpts.
                </p>
                <div className="flex flex-col gap-2">
                  {[
                    "What is our vacation policy and PTO rollover?",
                    "What are the payment terms and balance on the invoice?",
                    "How do AI agents exhibit transformational creativity?",
                  ].map((s) => (
                    <button
                      key={s}
                      onClick={() => ask(s)}
                      className="text-left text-xs px-3.5 py-2.5 rounded-lg border border-slate-200 hover:border-indigo-300 hover:bg-indigo-50/30 text-slate-700 transition-colors"
                    >
                      💡 {s}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[75%] bg-indigo-600 text-white text-sm px-4 py-2.5 rounded-2xl rounded-tr-xs shadow-xs leading-relaxed">
                  {m.content}
                </div>
              </div>
            ) : (
              <div key={m.id} className="flex flex-col gap-2.5 max-w-[85%]">
                <div className="bg-slate-50 border border-slate-200/80 px-4 py-3.5 rounded-2xl rounded-tl-xs shadow-xs relative group">
                  <AnswerBody
                    content={m.content || "Generating answer..."}
                    onCitationClick={(n) => handleCitationClick(n, m.citations)}
                  />

                  {m.streaming && (
                    <span className="inline-block w-1.5 h-4 bg-indigo-500 animate-pulse ml-1 align-middle" />
                  )}

                  {!m.streaming && (
                    <div className="flex items-center justify-between mt-3 pt-2 border-t border-slate-200/60 text-[11px] text-slate-400">
                      <div className="flex items-center gap-2">
                        {m.latency_ms ? <span>{(m.latency_ms / 1000).toFixed(1)}s</span> : null}
                        {m.retrieval_ms ? <span>· {m.retrieval_ms}ms retrieval</span> : null}
                        {m.prompt_tokens && m.completion_tokens ? (
                          <span>· {m.prompt_tokens + m.completion_tokens} tokens</span>
                        ) : null}
                      </div>
                      <button
                        onClick={() => copyToClipboard(m.content, m.id)}
                        className="text-slate-400 hover:text-slate-600 font-medium transition-colors"
                      >
                        {copiedId === m.id ? "✓ Copied" : "Copy"}
                      </button>
                    </div>
                  )}
                </div>

                {m.citations && m.citations.length > 0 && (
                  <div className="grid gap-2 sm:grid-cols-2 mt-1">
                    {m.citations.map((c) => (
                      <CitationCard
                        key={c.n}
                        c={c}
                        onClick={() => setSelectedCitation(c)}
                      />
                    ))}
                  </div>
                )}
              </div>
            ),
          )}
          <div ref={bottom} />
        </div>

        {/* Input Bar */}
        <div className="border-t border-slate-200 p-3 bg-slate-50/50">
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
              placeholder="Ask anything about your uploaded documents..."
              disabled={busy}
              className="flex-1 px-4 py-2.5 rounded-lg border border-slate-200 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300 focus:border-indigo-500 disabled:bg-slate-100 transition-all"
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              className="px-5 py-2.5 rounded-lg bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 disabled:opacity-40 transition-colors shadow-xs"
            >
              {busy ? "Thinking..." : "Send"}
            </button>
          </form>
        </div>
      </section>

      {/* Citation Inspector Drawer / Modal */}
      {selectedCitation && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-xs p-4">
          <div className="bg-white rounded-xl shadow-xl border border-slate-200 max-w-lg w-full p-5 space-y-4 animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-indigo-600 text-white font-bold text-xs grid place-items-center">
                  {selectedCitation.n}
                </span>
                <div>
                  <h4 className="text-sm font-bold text-slate-900 leading-tight">
                    {selectedCitation.document_name}
                  </h4>
                  <p className="text-[11px] text-slate-500">Page {selectedCitation.page}</p>
                </div>
              </div>
              <button
                onClick={() => setSelectedCitation(null)}
                className="text-slate-400 hover:text-slate-700 p-1 text-lg font-bold"
              >
                ✕
              </button>
            </div>

            <div>
              <p className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">
                Retrieved Passage Excerpt
              </p>
              <div className="bg-slate-50 border border-slate-200 rounded-lg p-3 text-xs leading-relaxed text-slate-700 max-h-60 overflow-y-auto whitespace-pre-wrap">
                {selectedCitation.snippet}
              </div>
            </div>

            <div className="flex justify-end pt-1">
              <button
                onClick={() => setSelectedCitation(null)}
                className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-semibold transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
