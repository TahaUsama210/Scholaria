import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { FileText, Library, Loader2, RotateCcw, Trash2 } from "lucide-react";

import { useDeleteDocument, useDocuments } from "@/hooks/useDocuments";
import { ApiError } from "@/lib/api";
import { formatRelativeTime, pluralize } from "@/lib/format";
import type { DocumentRead, DocumentStatus } from "@/types/api";
import { StatusBadge } from "./StatusBadge";

/** Announces ingestion finishing (ready/failed) to screen readers, once per transition. */
function useIngestAnnouncements(docs: readonly DocumentRead[] | undefined): string {
  const previous = useRef<Map<string, DocumentStatus> | null>(null);
  const [text, setText] = useState("");

  useEffect(() => {
    if (!docs) return;
    const before = previous.current;
    previous.current = new Map(docs.map((d) => [d.id, d.status]));
    if (!before) return;
    const messages: string[] = [];
    for (const d of docs) {
      const was = before.get(d.id);
      if (was === undefined || was === d.status) continue;
      if (d.status === "ready") messages.push(`“${d.title}” is ready to search.`);
      if (d.status === "failed") messages.push(`“${d.title}” failed to process.`);
    }
    if (messages.length > 0) setText(messages.join(" "));
  }, [docs]);

  return text;
}

export function DocumentList() {
  const { data, isPending, isError, error, refetch, isRefetching } = useDocuments();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const announcement = useIngestAnnouncements(data?.items);
  const docs = data?.items ?? [];

  let body: JSX.Element;
  if (isPending) {
    body = (
      <div aria-busy="true" className="space-y-2">
        <span className="sr-only">Loading documents…</span>
        {[0, 1, 2].map((i) => (
          <div key={i} className="card h-[4.5rem] animate-pulse bg-ink-100/60" aria-hidden="true" />
        ))}
      </div>
    );
  } else if (isError) {
    body = (
      <div role="alert" className="card p-3 text-sm">
        <p className="font-medium text-ink-900">Couldn't load your library.</p>
        <p className="mt-0.5 text-xs text-ink-600">
          {error instanceof ApiError ? error.message : "Please try again."}
        </p>
        <button
          type="button"
          onClick={() => void refetch()}
          disabled={isRefetching}
          className="btn-secondary mt-2 px-2.5 py-1 text-xs"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          Retry
        </button>
      </div>
    );
  } else if (docs.length === 0) {
    body = (
      <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-ink-300 px-4 py-6 text-center">
        <Library className="h-6 w-6 text-ink-400" aria-hidden="true" />
        <p className="text-sm font-medium text-ink-800">No documents yet</p>
        <p className="text-xs text-ink-600">
          Upload a course PDF and it will appear here while it's indexed.
        </p>
      </div>
    );
  } else {
    body = (
      <ul aria-label="Documents" className="space-y-2">
        {docs.map((d) => (
          <DocumentRow key={d.id} doc={d} onDeleted={() => headingRef.current?.focus()} />
        ))}
      </ul>
    );
  }

  return (
    <section aria-labelledby="library-heading" className="space-y-2">
      <div className="flex items-baseline justify-between gap-2 px-1">
        <h2 id="library-heading" ref={headingRef} tabIndex={-1} className="section-title">
          Library
        </h2>
        {data && data.total > 0 && (
          <span className="text-xs text-ink-500">
            {data.total > docs.length
              ? `Showing ${docs.length} of ${data.total}`
              : pluralize(data.total, "document")}
          </span>
        )}
      </div>
      {body}
      <p role="status" className="sr-only">
        {announcement}
      </p>
    </section>
  );
}

function DocumentRow({ doc, onDeleted }: { doc: DocumentRead; onDeleted(): void }) {
  const del = useDeleteDocument();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const deleteButton = useRef<HTMLButtonElement>(null);

  const cancel = () => {
    setConfirming(false);
    setError(null);
    deleteButton.current?.focus();
  };

  const confirm = async () => {
    setError(null);
    try {
      await del.mutateAsync(doc.id);
      onDeleted();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete the document.");
    }
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      cancel();
    }
  };

  return (
    <li className="card p-3" aria-label={doc.title} data-status={doc.status}>
      <div className="flex items-start gap-2.5">
        <FileText className="mt-0.5 h-4 w-4 shrink-0 text-ink-400" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-ink-900" title={doc.title}>
            {doc.title}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-ink-600">
            <StatusBadge status={doc.status} />
            {doc.course_code && (
              <span className="rounded bg-ink-100 px-1.5 py-0.5 font-medium text-ink-700">
                {doc.course_code}
              </span>
            )}
            {doc.status === "ready" && (
              <span>
                {pluralize(doc.page_count, "page")} · {pluralize(doc.chunk_count, "chunk")}
              </span>
            )}
          </div>
          {doc.status === "failed" && (
            <p className="mt-1.5 break-words text-xs text-red-700 dark:text-red-400">
              {doc.error_message || "Processing failed."}
            </p>
          )}
          <p className="mt-1 text-[11px] text-ink-500">
            <time dateTime={doc.created_at} title={new Date(doc.created_at).toLocaleString()}>
              Added {formatRelativeTime(doc.created_at)}
            </time>
          </p>
        </div>
        <button
          ref={deleteButton}
          type="button"
          onClick={() => setConfirming(true)}
          aria-expanded={confirming}
          aria-label={`Delete ${doc.title}`}
          className="icon-btn -mr-1 -mt-1 h-8 w-8 hover:bg-red-50 hover:text-red-700 dark:hover:bg-red-500/10 dark:hover:text-red-400"
        >
          <Trash2 className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>

      {confirming && (
        <div
          role="group"
          aria-label={`Confirm deleting ${doc.title}`}
          onKeyDown={onKeyDown}
          className="mt-2 rounded-lg border border-red-200 bg-red-50 p-2.5 dark:border-red-900/70 dark:bg-red-950/40"
        >
          <p className="text-xs text-red-800 dark:text-red-200">
            Delete this document
            {doc.chunk_count > 0 ? ` and its ${pluralize(doc.chunk_count, "indexed passage")}` : ""}?
            This can't be undone.
          </p>
          {error && (
            <p role="alert" className="mt-1 text-xs font-medium text-red-800 dark:text-red-200">
              {error}
            </p>
          )}
          <div className="mt-2 flex justify-end gap-2">
            <button
              type="button"
              onClick={cancel}
              autoFocus
              className="btn-secondary px-2.5 py-1 text-xs"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void confirm()}
              disabled={del.isPending}
              className="btn-danger px-2.5 py-1 text-xs"
            >
              {del.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
              {del.isPending ? "Deleting…" : "Delete"}
            </button>
          </div>
        </div>
      )}
    </li>
  );
}
