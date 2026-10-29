import { useEffect, useState } from "react";
import { fetchStats } from "../api";
import type { StatsOverview } from "../types";

export default function StatsPage() {
  const [stats, setStats] = useState<StatsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    fetchStats()
      .then((data) => {
        setStats(data);
        setError(null);
      })
      .catch((e) => setError(String(e.message ?? e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-bold text-slate-900">Analytics & System Stats</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            Real-time latency metrics, token consumption, and retrieval tracking.
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="px-3.5 py-1.5 rounded-lg border border-slate-200 hover:border-slate-300 bg-white text-xs font-semibold text-slate-700 shadow-xs transition-colors flex items-center gap-1.5"
        >
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            className={loading ? "animate-spin" : ""}
          >
            <path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
          </svg>
          Refresh
        </button>
      </div>

      {error && (
        <div className="mb-6 rounded-lg bg-rose-50 border border-rose-200 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      )}

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-xs">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Indexed Documents</p>
          <h3 className="text-2xl font-bold text-slate-900 mt-2">
            {stats ? stats.ready_documents : "—"}
            <span className="text-xs font-normal text-slate-400 ml-2">
              ({stats ? stats.total_pages : 0} pages)
            </span>
          </h3>
          <p className="text-xs text-slate-500 mt-1">
            {stats ? stats.total_chunks : 0} chunks embedded in pgvector
          </p>
        </div>

        <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-xs">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Queries Processed</p>
          <h3 className="text-2xl font-bold text-indigo-600 mt-2">
            {stats ? stats.total_queries : "—"}
          </h3>
          <p className="text-xs text-slate-500 mt-1">
            Hybrid BM25 + pgvector reciprocal rank fusion
          </p>
        </div>

        <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-xs">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Average Latency</p>
          <h3 className="text-2xl font-bold text-emerald-600 mt-2">
            {stats ? `${stats.latency.avg_total_ms}ms` : "—"}
          </h3>
          <p className="text-xs text-slate-500 mt-1">
            {stats ? `${stats.latency.avg_retrieval_ms}ms search · ${stats.latency.avg_llm_ms}ms generation` : "—"}
          </p>
        </div>

        <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-xs">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Token Usage</p>
          <h3 className="text-2xl font-bold text-slate-900 mt-2">
            {stats ? stats.tokens.total_tokens.toLocaleString() : "—"}
          </h3>
          <p className="text-xs text-slate-500 mt-1">
            {stats ? `Est. cloud cost: $${stats.tokens.estimated_cost_usd}` : "—"}
          </p>
        </div>
      </div>

      {/* Latency Breakdown Bar */}
      {stats && stats.latency.avg_total_ms > 0 && (
        <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-xs mb-8">
          <h4 className="text-sm font-bold text-slate-900 mb-3">Mean Latency Breakdown</h4>
          <div className="w-full bg-slate-100 rounded-full h-3 overflow-hidden flex">
            <div
              className="bg-indigo-500 h-full transition-all"
              style={{
                width: `${Math.min(100, Math.round((stats.latency.avg_retrieval_ms / stats.latency.avg_total_ms) * 100))}%`,
              }}
              title={`Retrieval: ${stats.latency.avg_retrieval_ms}ms`}
            />
            <div
              className="bg-emerald-500 h-full transition-all flex-1"
              title={`LLM Generation: ${stats.latency.avg_llm_ms}ms`}
            />
          </div>
          <div className="flex justify-between items-center text-xs text-slate-500 mt-2.5">
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-indigo-500" />
              Hybrid Retrieval ({stats.latency.avg_retrieval_ms}ms)
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
              LLM Streaming Generation ({stats.latency.avg_llm_ms}ms)
            </span>
            <span className="font-semibold text-slate-800">
              Total: {stats.latency.avg_total_ms}ms
            </span>
          </div>
        </div>
      )}

      {/* Recent Queries Table */}
      <div className="bg-white border border-slate-200 rounded-xl shadow-xs overflow-hidden">
        <div className="px-5 py-3.5 border-b border-slate-100 flex items-center justify-between">
          <h4 className="text-sm font-bold text-slate-900">Recent Query Logs</h4>
          <span className="text-xs text-slate-400">
            {stats?.recent_queries.length ?? 0} logged
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-slate-50/70 border-b border-slate-100 text-slate-500 font-semibold">
                <th className="py-2.5 px-4">Question</th>
                <th className="py-2.5 px-4">Model</th>
                <th className="py-2.5 px-4">Retrieval</th>
                <th className="py-2.5 px-4">Generation</th>
                <th className="py-2.5 px-4">Total</th>
                <th className="py-2.5 px-4">Tokens</th>
                <th className="py-2.5 px-4">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {stats?.recent_queries.length === 0 && (
                <tr>
                  <td colSpan={7} className="text-center py-8 text-slate-400">
                    No query logs recorded yet. Ask questions in the Chat tab to view logs.
                  </td>
                </tr>
              )}
              {stats?.recent_queries.map((q) => (
                <tr key={q.id} className="hover:bg-slate-50/50 transition-colors">
                  <td className="py-2.5 px-4 font-medium text-slate-800 max-w-[240px] truncate" title={q.question}>
                    {q.question}
                  </td>
                  <td className="py-2.5 px-4 font-mono text-[11px] text-slate-600">{q.model}</td>
                  <td className="py-2.5 px-4 text-indigo-700 font-semibold">{q.retrieval_ms}ms</td>
                  <td className="py-2.5 px-4 text-emerald-700 font-semibold">{q.llm_ms}ms</td>
                  <td className="py-2.5 px-4 font-bold text-slate-800">{q.total_ms}ms</td>
                  <td className="py-2.5 px-4 text-slate-600">
                    {q.prompt_tokens + q.completion_tokens}
                  </td>
                  <td className="py-2.5 px-4 text-slate-400">
                    {new Date(q.created_at).toLocaleTimeString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
