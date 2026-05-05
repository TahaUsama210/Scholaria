import { Trash2, FileText, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";
import type { DocumentRead, DocumentStatus } from "@/types/api";
import { useDeleteDocument, useDocuments } from "@/hooks/useDocuments";

const STATUS_ICON: Record<DocumentStatus, JSX.Element> = {
  pending: <Loader2 className="h-3.5 w-3.5 animate-spin text-ink-400" />,
  processing: <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />,
  ready: <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />,
  failed: <AlertCircle className="h-3.5 w-3.5 text-red-500" />,
};

export function DocumentList() {
  const { data, isLoading, isError } = useDocuments();
  const del = useDeleteDocument();

  if (isLoading) return <p className="px-1 text-xs text-ink-500">Loading…</p>;
  if (isError) return <p className="px-1 text-xs text-red-600">Failed to load documents.</p>;

  const docs = data?.items ?? [];
  if (docs.length === 0) {
    return (
      <p className="px-1 text-xs text-ink-500">
        No documents yet. Upload a PDF to get started.
      </p>
    );
  }

  return (
    <ul className="space-y-2">
      {docs.map((d) => (
        <Row key={d.id} doc={d} onDelete={() => del.mutate(d.id)} />
      ))}
    </ul>
  );
}

function Row({ doc, onDelete }: { doc: DocumentRead; onDelete: () => void }) {
  return (
    <li className="card flex items-center gap-2 p-2.5 text-sm">
      <FileText className="h-4 w-4 shrink-0 text-ink-400" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          {STATUS_ICON[doc.status]}
          <span className="truncate font-medium text-ink-800" title={doc.title}>
            {doc.title}
          </span>
        </div>
        <div className="mt-0.5 text-[11px] text-ink-500">
          {doc.course_code ? `${doc.course_code} · ` : ""}
          {doc.page_count} pages · {doc.chunk_count} chunks
        </div>
        {doc.error_message && (
          <p className="mt-1 text-[11px] text-red-600">{doc.error_message}</p>
        )}
      </div>
      <button
        type="button"
        onClick={onDelete}
        className="text-ink-400 hover:text-red-600"
        aria-label="Delete document"
      >
        <Trash2 className="h-4 w-4" />
      </button>
    </li>
  );
}
