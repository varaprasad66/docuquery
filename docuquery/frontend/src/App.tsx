import { useCallback, useEffect, useState } from "react";
import ChatPage from "./pages/Chat";
import DocumentsPage from "./pages/Documents";
import ExtractPage from "./pages/Extract";
import StatsPage from "./pages/Stats";
import {
  clearStoredToken,
  fetchHealth,
  fetchMe,
  getStoredToken,
  loginUser,
  refreshSession,
  registerUser,
} from "./api";
import type { User } from "./types";

type Tab = "documents" | "chat" | "extract" | "analytics";
type Health = Awaited<ReturnType<typeof fetchHealth>>;

export default function App() {
  const [tab, setTab] = useState<Tab>("documents");
  const [health, setHealth] = useState<Health | null>(null);
  const [currentUser, setCurrentUser] = useState<User | null>(null);

  // Auth modal state
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [authEmail, setAuthEmail] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authDisplayName, setAuthDisplayName] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [authBusy, setAuthBusy] = useState(false);

  const refreshHealth = useCallback(() => {
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  const refreshUser = useCallback(() => {
    fetchMe()
      .then(setCurrentUser)
      .catch(() => setCurrentUser(null));
  }, []);

  useEffect(() => {
    // No stored access token: try extending the session via the HttpOnly
    // refresh cookie before falling back to signed-out state.
    if (!getStoredToken()) {
      refreshSession()
        .then((session) => {
          if (session) setCurrentUser(session.user);
        })
        .catch(() => undefined);
    }
    refreshHealth();
    refreshUser();
    const timer = setInterval(refreshHealth, 30_000);
    return () => clearInterval(timer);
  }, [refreshHealth, refreshUser]);

  const handleAuthSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError(null);
    setAuthBusy(true);

    try {
      if (authMode === "login") {
        const res = await loginUser(authEmail, authPassword);
        setCurrentUser(res.user);
      } else {
        const res = await registerUser(authEmail, authPassword, authDisplayName || undefined);
        setCurrentUser(res.user);
      }
      setAuthModalOpen(false);
      setAuthEmail("");
      setAuthPassword("");
      setAuthDisplayName("");
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : String(err));
    } finally {
      setAuthBusy(false);
    }
  };

  const handleLogout = () => {
    clearStoredToken();
    setCurrentUser(null);
  };

  const statusColor = !health
    ? "bg-slate-400"
    : health.status === "ok"
      ? "bg-emerald-500"
      : "bg-amber-500";

  const editionLabels: Record<string, { name: string; badge: string }> = {
    basic: { name: "Starter", badge: "bg-slate-100 text-slate-700 border-slate-200" },
    professional: { name: "Professional", badge: "bg-sky-50 text-sky-700 border-sky-200" },
    enterprise: { name: "Enterprise", badge: "bg-violet-50 text-violet-700 border-violet-200" },
  };
  const edition = health?.edition ? editionLabels[health.edition] ?? editionLabels.enterprise : null;

  return (
    <div className="min-h-screen flex flex-col bg-slate-50/40 text-slate-800">
      <header className="bg-white border-b border-slate-200 sticky top-0 z-40 shadow-xs">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-4">
            <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-indigo-600 text-white grid place-items-center font-bold text-sm shadow-xs">
              DQ
            </div>
            <div>
              <h1 className="font-bold text-slate-900 leading-tight text-sm">DocuQuery</h1>
              <p className="text-[11px] text-slate-500 leading-tight">
                Local-first Document Intelligence (RAG + OCR)
              </p>
            </div>
            {edition && (
              <span
                title={`Licensed edition: ${health?.edition}`}
                className={`ml-1 px-2 py-0.5 rounded-full border text-[10px] font-bold uppercase tracking-wide ${edition.badge}`}
              >
                {edition.name}
              </span>
            )}
          </div>

          <nav className="ml-6 flex gap-1">
            {(["documents", "chat", "extract", "analytics"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold capitalize transition-colors ${
                  tab === t
                    ? "bg-indigo-50 text-indigo-700 font-bold"
                    : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                {t}
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            {/* System Health */}
            <div className="hidden sm:flex items-center gap-2 text-[11px] text-slate-500 bg-slate-50 px-2.5 py-1 rounded-full border border-slate-200/60">
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

            {/* Auth Indicator / Profile */}
            {currentUser ? (
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-slate-700 bg-indigo-50 text-indigo-800 px-2.5 py-1 rounded-md border border-indigo-100">
                  👤 {currentUser.display_name || currentUser.email}
                </span>
                <button
                  onClick={handleLogout}
                  className="text-xs text-slate-500 hover:text-rose-600 font-medium px-2 py-1 rounded hover:bg-slate-100 transition-colors"
                >
                  Sign Out
                </button>
              </div>
            ) : (
              <button
                onClick={() => {
                  setAuthError(null);
                  setAuthModalOpen(true);
                }}
                className="text-xs font-semibold text-indigo-700 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 px-3 py-1.5 rounded-lg transition-colors shadow-xs"
              >
                Sign In
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="flex-1">
        {tab === "documents" && <DocumentsPage />}
        {tab === "chat" && <ChatPage />}
        {tab === "extract" && <ExtractPage />}
        {tab === "analytics" && <StatsPage />}
      </main>

      {/* Auth Modal */}
      {authModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-xs p-4">
          <div className="bg-white rounded-xl shadow-xl border border-slate-200 max-w-sm w-full p-6 animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-bold text-slate-900">
                {authMode === "login" ? "Sign In to DocuQuery" : "Create DocuQuery Account"}
              </h3>
              <button
                onClick={() => setAuthModalOpen(false)}
                className="text-slate-400 hover:text-slate-600 font-bold"
              >
                ✕
              </button>
            </div>

            {authError && (
              <div className="mb-4 rounded-lg bg-rose-50 border border-rose-200 p-2.5 text-xs text-rose-700">
                {authError}
              </div>
            )}

            <form onSubmit={handleAuthSubmit} className="space-y-3">
              {authMode === "register" && (
                <div>
                  <label className="block text-xs font-semibold text-slate-700 mb-1">
                    Display Name
                  </label>
                  <input
                    value={authDisplayName}
                    onChange={(e) => setAuthDisplayName(e.target.value)}
                    placeholder="Your name"
                    className="w-full px-3 py-2 rounded-lg border border-slate-200 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-300 focus:border-indigo-500"
                  />
                </div>
              )}

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Email Address
                </label>
                <input
                  type="email"
                  required
                  value={authEmail}
                  onChange={(e) => setAuthEmail(e.target.value)}
                  placeholder="user@example.com"
                  className="w-full px-3 py-2 rounded-lg border border-slate-200 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-300 focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Password
                </label>
                <input
                  type="password"
                  required
                  value={authPassword}
                  onChange={(e) => setAuthPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full px-3 py-2 rounded-lg border border-slate-200 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-300 focus:border-indigo-500"
                />
              </div>

              <button
                type="submit"
                disabled={authBusy}
                className="w-full mt-2 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-semibold transition-colors shadow-xs disabled:opacity-50"
              >
                {authBusy
                  ? "Processing..."
                  : authMode === "login"
                    ? "Sign In"
                    : "Create Account"}
              </button>
            </form>

            <div className="mt-4 pt-3 border-t border-slate-100 text-center">
              <button
                onClick={() => {
                  setAuthMode(authMode === "login" ? "register" : "login");
                  setAuthError(null);
                }}
                className="text-xs text-indigo-600 hover:underline font-medium"
              >
                {authMode === "login"
                  ? "Don't have an account? Register"
                  : "Already registered? Sign in"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
