import { forwardRef, useEffect, useId, useState } from "react";
import { ChevronDown, FileText } from "lucide-react";

import { cleanSnippet, formatScore } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Citation } from "@/types/api";

interface Props {
  citation: Citation;
  id?: string;
  /** true/false once the answer is known to cite (or not cite) this source; null while unknown. */
  cited?: boolean | null;
  /** Selected via an inline citation chip. Active cards expand and get a highlight ring. */
  active?: boolean;
}

export const CitationCard = forwardRef<HTMLElement, Props>(function CitationCard(
  { citation, id, cited = null, active = false },
  ref,
) {
  const [open, setOpen] = useState(active);
  const titleId = useId();
  const bodyId = useId();
  const score = formatScore(citation.score);

  useEffect(() => {
    if (active) setOpen(true);
  }, [active]);

  return (
    <article
      ref={ref}
      id={id}
      tabIndex={-1}
      aria-labelledby={titleId}
      aria-current={active ? "true" : undefined}
      data-active={active ? "true" : undefined}
      data-marker={citation.marker}
      className={cn(
        "card scroll-mt-4 p-3 text-sm transition-shadow",
        active ? "ring-2 ring-accent" : "hover:border-ink-300",
        cited === false && !active && "opacity-75",
      )}
    >
      <div className="flex items-start gap-2.5">
        <span
          aria-hidden="true"
          className={cn(
            "mt-0.5 inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded-md px-1",
            "text-[11px] font-semibold tabular-nums",
            cited === false ? "bg-ink-100 text-ink-600" : "bg-accent text-accent-fg",
          )}
        >
          {citation.marker}
        </span>
        <div className="min-w-0 flex-1">
          <h3
            id={titleId}
            className={cn("font-medium leading-snug text-ink-900", !open && "truncate")}
            title={citation.document_title}
          >
            <span className="sr-only">Source {citation.marker}: </span>
            {citation.document_title}
          </h3>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-ink-500">
            {citation.page_number != null && <span>Page {citation.page_number}</span>}
            {cited === true && (
              <span className="font-medium text-emerald-700 dark:text-emerald-400">Cited</span>
            )}
            {cited === false && <span>Not cited</span>}
          </p>
        </div>
      </div>

      <p
        id={bodyId}
        className={cn(
          "mt-2 text-xs leading-relaxed text-ink-700",
          !open && "line-clamp-3",
        )}
      >
        {cleanSnippet(citation.snippet)}
      </p>

      {open && (
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 border-t border-ink-100 pt-2 text-xs">
          <dt className="text-ink-500">Document</dt>
          <dd className="min-w-0 break-words text-ink-800">{citation.document_title}</dd>
          <dt className="text-ink-500">Page</dt>
          <dd className="text-ink-800">{citation.page_number ?? "—"}</dd>
          {score && (
            <>
              <dt className="text-ink-500" title="Retrieval/reranker score; higher is more relevant">
                Relevance
              </dt>
              <dd className="font-mono tabular-nums text-ink-800">{score}</dd>
            </>
          )}
        </dl>
      )}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={bodyId}
        className="mt-2 inline-flex items-center gap-1 rounded text-xs font-medium text-accent-text hover:underline"
      >
        <FileText className="h-3.5 w-3.5" aria-hidden="true" />
        {open ? "Hide details" : "Show details"}
        <ChevronDown
          className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")}
          aria-hidden="true"
        />
      </button>
    </article>
  );
});
