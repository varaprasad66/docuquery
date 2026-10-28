import { useCallback, useEffect, useState } from "react";
import ChatPage from "./pages/Chat";
import DocumentsPage from "./pages/Documents";
import { fetchHealth } from "./api";

type Tab = "documents" | "chat";
type Health = Awaited<ReturnType<typeof fetchHealth>>;

export default function App() {
  const [tab, setTab] = useState<Tab>("documents");
  const [health, setHealth] = useState<Health | null>(null);

  const refresh = useCallback(() => {
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 30_000);
    return () => clearInterval(timer);
  }, [refresh]);

  const statusColor = !health
    ? "bg-slate-400"
    : health.status === "ok"
      ? "bg-emerald-500"
      : "bg-amber-500";

  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-4">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-indigo-600 text-white grid place-items-center font-bold">
              D
            </div>
            <div>
              <h1 className="font-semibold text-slate-900 leading-tight">DocuQuery</h1>
              <p className="text-xs text-slate-500 leading-tight">
                Local-first document intelligence
              </p>
            </div>
          </div>

          <nav className="ml-6 flex gap-1">
            {(["documents", "chat"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-4 py-1.5 rounded-md text-sm font-medium capitalize transition-colors ${
                  tab === t
                    ? "bg-indigo-50 text-indigo-700"
                    : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                {t}
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2 text-xs text-slate-500">
            <span className={`w-2 h-2 rounded-full ${statusColor}`} />
            {health ? (
              <>
                <span className="font-mono">{health.model}</span>
                <span className="text-slate-300">|</span>
                <span>
                  db {health.components.database} · llm {health.components.llm}
                </span>
              </>
            ) : (
              <span>connecting…</span>
            )}
          </div>
        </div>
      </header>

      <main className="flex-1">
        {tab === "documents" ? <DocumentsPage /> : <ChatPage />}
      </main>
    </div>
  );
}
