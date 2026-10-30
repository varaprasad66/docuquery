import { useCallback, useEffect, useRef, useState } from "react";
import { deleteDocument, fetchDocuments, importDocumentFromUrl, retryDocument, uploadDocument } from "../api";
import type { DocRecord } from "../types";

const ACCEPTED_TYPES =
  "application/pdf,.pdf,.docx,.pptx,.xlsx,.html,.htm,.txt,.md";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function StatusBadge({
  doc,
  onRetry,
}: {
  doc: DocRecord;
  onRetry: (id: string) => void;
}) {
  if (doc.status === "ready") {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-700 bg-emerald-50 border border-emerald-200 px-2.5 py-1 rounded-full">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Ready
      </span>
    );
  }

  if (doc.status === "failed") {
    return (
      <div className="flex items-center gap-2">
        <span
          title={doc.error ?? "Processing failed"}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-rose-700 bg-rose-50 border border-rose-200 px-2.5 py-1 rounded-full cursor-help max-w-[200px] truncate"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-rose-500" />
          Failed
        </span>
        <button
          onClick={() => onRetry(doc.id)}
          className="text-xs font-medium text-indigo-600 hover:text-indigo-800 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 px-2 py-0.5 rounded transition-colors"
          title="Retry processing this document"
        >
          Retry
        </button>
      </div>
    );
  }

  // Processing with animated progress bar
  const pct = Math.max(1, Math.round(doc.progress * 100));
  return (
    <div className="flex items-center gap-2 min-w-[130px]">
      <div className="flex-1 bg-slate-100 rounded-full h-2 overflow-hidden border border-slate-200">
        <div
          className="bg-indigo-600 h-full rounded-full transition-all duration-300 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs font-semibold text-indigo-700 tabular-nums">
        {pct}%
      </span>
    </div>
  );
}

export default function DocumentsPage() {
  const [docs, setDocs] = useState<DocRecord[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [urlValue, setUrlValue] = useState("");
  const [importingUrl, setImportingUrl] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    fetchDocuments()
      .then(setDocs)
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while any document is still processing
  useEffect(() => {
    if (!docs.some((d) => d.status === "processing")) return;
    const timer = setInterval(load, 1500);
    return () => clearInterval(timer);
  }, [docs, load]);

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files?.length) return;
      setUploading(true);
      setError(null);
      for (const file of Array.from(files)) {
        try {
          await uploadDocument(file);
        } catch (e) {
          setError(`${file.name}: ${e instanceof Error ? e.message : String(e)}`);
        }
      }
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
      load();
    },
    [load],
  );

  const handleRetry = async (id: string) => {
    try {
      await retryDocument(id);
      load();
    } catch (e) {
      setError(`Retry failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleImportUrl = async (e: React.FormEvent) => {
    e.preventDefault();
    const url = urlValue.trim();
    if (!url) return;
    setImportingUrl(true);
    setError(null);
    try {
      await importDocumentFromUrl(url);
      setUrlValue("");
      load();
    } catch (err) {
      setError(`URL import failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setImportingUrl(false);
    }
  };

  const remove = async (id: string) => {
    try {
      await deleteDocument(id);
      setDocs((prev) => prev.filter((d) => d.id !== id));
    } catch (e) {
      setError(String(e));
    }
  };

  const readyCount = docs.filter((d) => d.status === "ready").length;
  const processingCount = docs.filter((d) => d.status === "processing").length;
  const totalChunks = docs.reduce((sum, d) => sum + d.chunk_count, 0);

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <div className="flex items-end justify-between mb-6">
        <div>
          <h2 className="text-xl font-bold text-slate-900">Documents</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            {docs.length} uploaded · {readyCount} ready
            {processingCount > 0 ? ` · ${processingCount} processing` : ""} · {totalChunks} chunks indexed
          </p>
        </div>
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          handleFiles(e.dataTransfer.files);
        }}
        onClick={() => fileInput.current?.click()}
        className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-all mb-6 ${
          dragOver
            ? "border-indigo-500 bg-indigo-50/50 scale-[1.005]"
            : "border-slate-300 hover:border-indigo-400 hover:bg-slate-50/50"
        }`}
      >
        <input
          ref={fileInput}
          type="file"
          accept={ACCEPTED_TYPES}
          multiple
          hidden
          onChange={(e) => handleFiles(e.target.files)}
        />
        {uploading ? (
          <div className="flex flex-col items-center gap-2.5">
            <span className="w-6 h-6 border-2 border-indigo-600 border-t-transparent rounded-full animate-spin" />
            <p className="text-sm font-medium text-slate-700">Uploading file(s)...</p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <div className="w-10 h-10 rounded-full bg-indigo-50 text-indigo-600 grid place-items-center mb-1">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12" />
              </svg>
            </div>
            <p className="text-sm font-semibold text-slate-800">
              Drop files here, or click to browse
            </p>
            <p className="text-xs text-slate-400">
              Supports PDF, DOCX, PPTX, XLSX, HTML, TXT and MD up to 50 MB · Ingestion with OCR and hybrid pgvector indexing
            </p>
          </div>
        )}
      </div>

      <form onSubmit={handleImportUrl} className="flex gap-2 mb-6">
        <input
          type="url"
          required
          value={urlValue}
          onChange={(e) => setUrlValue(e.target.value)}
          placeholder="…or import from a URL (https://example.com/report.pdf)"
          className="flex-1 text-sm border border-slate-300 rounded-lg px-3 py-2.5 bg-white text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
        />
        <button
          type="submit"
          disabled={importingUrl || !urlValue.trim()}
          className="text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg px-4 py-2.5 transition-colors"
        >
          {importingUrl ? "Importing…" : "Import URL"}
        </button>
      </form>

      {error && (
        <div className="mb-4 rounded-lg bg-rose-50 border border-rose-200 px-4 py-3 text-sm text-rose-700 flex items-center justify-between">
          <span>{error}</span>
          <button
            onClick={() => setError(null)}
            className="text-rose-500 hover:text-rose-700 font-bold ml-2"
          >
            ×
          </button>
        </div>
      )}

      <div className="bg-white border border-slate-200 rounded-xl divide-y divide-slate-100 shadow-sm overflow-hidden">
        {docs.length === 0 && (
          <div className="px-4 py-12 text-center">
            <p className="text-sm font-medium text-slate-500">No documents indexed yet.</p>
            <p className="text-xs text-slate-400 mt-1">Upload a PDF above to begin chatting across your corpus.</p>
          </div>
        )}
        {docs.map((doc) => (
          <div key={doc.id} className="px-4 py-3.5 flex items-center gap-4 hover:bg-slate-50/50 transition-colors">
            <div className="w-9 h-9 rounded-lg bg-indigo-50 text-indigo-700 grid place-items-center text-xs font-bold shrink-0">
              PDF
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-slate-900 truncate">{doc.original_name}</p>
              <p className="text-xs text-slate-500 mt-0.5">
                {formatSize(doc.size_bytes)}
                {doc.status === "ready" && ` · ${doc.page_count} pages · ${doc.chunk_count} chunks`}
                {" · "}{new Date(doc.created_at).toLocaleDateString()}
              </p>
            </div>
            <StatusBadge doc={doc} onRetry={handleRetry} />
            <button
              onClick={() => remove(doc.id)}
              className="text-slate-400 hover:text-rose-600 transition-colors p-1.5 rounded-md hover:bg-rose-50 shrink-0"
              title="Delete document"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M3 6h18M8 6V4h8v2m-9 0v14a2 2 0 002 2h6a2 2 0 002-2V6" />
              </svg>
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
