import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchDocuments, fetchExtractionTemplates, runExtraction } from "../api";
import type { DocRecord, ExtractResult } from "../types";

interface TemplateInfo {
  description: string;
  schema: Record<string, unknown>;
}

function flattenValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v, null, 1);
  return String(v);
}

function toCsv(result: ExtractResult): string {
  const rows: string[][] = [["field", "value"]];
  const entries = Object.entries(result.data ?? {});
  for (const [k, v] of entries) {
    const cell = typeof v === "object" ? JSON.stringify(v) : String(v ?? "");
    rows.push([k, cell]);
  }
  for (const c of result.citations ?? []) {
    rows.push([`_source:${c.field}`, `${c.document_name} · ${c.location}`]);
  }
  return rows
    .map((r) => r.map((cell) => `"${cell.replace(/"/g, '""')}"`).join(","))
    .join("\n");
}

function download(name: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export default function ExtractPage() {
  const [docs, setDocs] = useState<DocRecord[]>([]);
  const [templates, setTemplates] = useState<Record<string, TemplateInfo>>({});
  const [docId, setDocId] = useState("");
  const [template, setTemplate] = useState<string>("invoice");
  const [result, setResult] = useState<ExtractResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    fetchDocuments()
      .then((all) => setDocs(all.filter((d) => d.status === "ready")))
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    load();
    fetchExtractionTemplates()
      .then(setTemplates)
      .catch(() => setTemplates({}));
  }, [load]);

  const readyDocs = useMemo(() => docs.filter((d) => d.status === "ready"), [docs]);

  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!docId) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await runExtraction([docId], template);
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const fields = result ? Object.entries(result.data ?? {}) : [];
  const citations = result?.citations ?? [];

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <div className="mb-6">
        <h2 className="text-xl font-bold text-slate-900">Extract</h2>
        <p className="text-sm text-slate-500 mt-0.5">
          Pull structured fields out of a document with a template — results include per-field source citations.
        </p>
      </div>

      <form onSubmit={run} className="flex flex-wrap items-end gap-3 mb-6">
        <div className="flex-1 min-w-[220px]">
          <label className="block text-xs font-semibold text-slate-700 mb-1">Document</label>
          <select
            value={docId}
            onChange={(e) => setDocId(e.target.value)}
            className="w-full text-sm border border-slate-300 rounded-lg px-3 py-2.5 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="">Choose a ready document…</option>
            {readyDocs.map((d) => (
              <option key={d.id} value={d.id}>
                {d.original_name}
              </option>
            ))}
          </select>
        </div>
        <div className="min-w-[180px]">
          <label className="block text-xs font-semibold text-slate-700 mb-1">Template</label>
          <select
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
            className="w-full text-sm border border-slate-300 rounded-lg px-3 py-2.5 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            {Object.keys(templates).length === 0 && <option value="invoice">invoice</option>}
            {Object.entries(templates).map(([name, info]) => (
              <option key={name} value={name} title={info.description}>
                {name}
              </option>
            ))}
          </select>
        </div>
        <button
          type="submit"
          disabled={busy || !docId}
          className="text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg px-5 py-2.5 transition-colors"
        >
          {busy ? "Extracting…" : "Extract"}
        </button>
      </form>

      {error && (
        <div className="mb-4 rounded-lg bg-rose-50 border border-rose-200 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      )}

      {result && (
        <div className="bg-white border border-slate-200 rounded-xl shadow-xs overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 bg-slate-50/60">
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold uppercase tracking-wide text-slate-500">
                {result.template}
              </span>
              <span
                className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full border ${
                  result.confidence === "high"
                    ? "text-emerald-700 bg-emerald-50 border-emerald-200"
                    : result.confidence === "medium"
                      ? "text-amber-700 bg-amber-50 border-amber-200"
                      : "text-rose-700 bg-rose-50 border-rose-200"
                }`}
              >
                confidence: {result.confidence}
              </span>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => download("extracted.json", JSON.stringify(result, null, 2), "application/json")}
                className="text-xs font-semibold text-indigo-700 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 px-2.5 py-1 rounded transition-colors"
              >
                Export JSON
              </button>
              <button
                onClick={() => download("extracted.csv", toCsv(result), "text/csv")}
                className="text-xs font-semibold text-indigo-700 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 px-2.5 py-1 rounded transition-colors"
              >
                Export CSV
              </button>
            </div>
          </div>

          <table className="w-full text-sm">
            <tbody>
              {fields.map(([key, value]) => (
                <tr key={key} className="border-b border-slate-100 last:border-0">
                  <td className="px-4 py-2.5 font-semibold text-slate-700 w-1/3 align-top">{key}</td>
                  <td className="px-4 py-2.5 text-slate-600 whitespace-pre-wrap font-mono text-xs">
                    {flattenValue(value)}
                  </td>
                </tr>
              ))}
              {fields.length === 0 && (
                <tr>
                  <td className="px-4 py-4 text-slate-400 text-xs" colSpan={2}>
                    No fields extracted.
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          {citations.length > 0 && (
            <div className="px-4 py-3 border-t border-slate-100 bg-slate-50/60">
              <p className="text-[11px] font-bold uppercase tracking-wide text-slate-500 mb-1.5">Sources</p>
              <ul className="space-y-1">
                {citations.map((c, i) => (
                  <li key={i} className="text-xs text-slate-600">
                    <span className="font-semibold text-slate-700">{c.field}</span> → {c.document_name} · {c.location}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
