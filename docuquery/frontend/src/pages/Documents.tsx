import { useCallback, useEffect, useRef, useState } from "react";
import { deleteDocument, fetchDocuments, uploadDocument } from "../api";
import type { DocRecord } from "../types";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function StatusBadge({ doc }: { doc: DocRecord }) {
  if (doc.status === "ready")
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Ready
      </span>
    );
  if (doc.status === "failed")
    return (
      <span
        title={doc.error ?? ""}
        className="inline-flex items-center gap-1 text-xs font-medium text-rose-700 bg-rose-50 px-2 py-0.5 rounded-full"
      >
        <span className="w-1.5 h-1.5 rounded-full bg-rose-500" /> Failed
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-indigo-700 bg-indigo-50 px-2 py-0.5 rounded-full">
      <span className="w-3 h-3 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
      {Math.round(doc.progress * 100)}%
    </span>
  );
}

export default function DocumentsPage() {
  const [docs, setDocs] = useState<DocRecord[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
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
      load();
    },
    [load],
  );

  const remove = async (id: string) => {
    try {
      await deleteDocument(id);
      setDocs((prev) => prev.filter((d) => d.id !== id));
    } catch (e) {
      setError(String(e));
    }
  };

  const readyCount = docs.filter((d) => d.status === "ready").length;
  const totalChunks = docs.reduce((sum, d) => sum + d.chunk_count, 0);

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <div className="flex items-end justify-between mb-4">
        <div>
          <h2 className="text-xl font-semibold">Documents</h2>
          <p className="text-sm text-slate-500">
            {docs.length} uploaded · {readyCount} ready · {totalChunks} chunks indexed
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
        className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors mb-6 ${
          dragOver ? "border-indigo-400 bg-indigo-50" : "border-slate-300 hover:border-indigo-300 hover:bg-slate-50"
        }`}
      >
        <input
          ref={fileInput}
          type="file"
          accept="application/pdf"
          multiple
          hidden
          onChange={(e) => handleFiles(e.target.files)}
        />
        {uploading ? (
          <div className="flex flex-col items-center gap-2">
            <span className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
            <p className="text-sm text-slate-600">Uploading…</p>
          </div>
        ) : (
          <>
            <p className="text-sm font-medium text-slate-700">
              Drop PDFs here, or click to browse
            </p>
            <p className="text-xs text-slate-400 mt-1">
              Digital and scanned PDFs · processed with OCR + hybrid search indexing
            </p>
          </>
        )}
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-rose-50 border border-rose-200 px-4 py-2 text-sm text-rose-700">
          {error}
        </div>
      )}

      <div className="bg-white border border-slate-200 rounded-xl divide-y divide-slate-100">
        {docs.length === 0 && (
          <p className="px-4 py-10 text-center text-sm text-slate-400">
            No documents yet — upload your first PDF above.
          </p>
        )}
        {docs.map((doc) => (
          <div key={doc.id} className="px-4 py-3 flex items-center gap-4">
            <div className="w-9 h-9 rounded-lg bg-slate-100 grid place-items-center text-slate-500 text-xs font-bold">
              PDF
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium truncate">{doc.original_name}</p>
              <p className="text-xs text-slate-500">
                {formatSize(doc.size_bytes)}
                {doc.status === "ready" && ` · ${doc.page_count} pages · ${doc.chunk_count} chunks`}
                {new Date(doc.created_at).toLocaleString()}
              </p>
            </div>
            <StatusBadge doc={doc} />
            <button
              onClick={() => remove(doc.id)}
              className="text-slate-400 hover:text-rose-600 transition-colors p-1"
              title="Delete document"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M3 6h18M8 6V4h8v2m-9 0v14a2 2 0 002 2h6a2 2 0 002-2V6" />
              </svg>
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
