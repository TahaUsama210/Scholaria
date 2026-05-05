import type { Citation } from "@/types/api";
import { cn } from "@/lib/utils";

interface Props {
  citation: Citation;
  highlighted?: boolean;
}

export function CitationCard({ citation, highlighted }: Props) {
  return (
    <div
      className={cn(
        "card p-3 text-sm transition-all",
        highlighted ? "ring-2 ring-accent" : "hover:border-ink-300",
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded bg-accent text-[11px] font-semibold text-accent-fg">
            {citation.marker}
          </span>
          <span className="truncate font-medium text-ink-900" title={citation.document_title}>
            {citation.document_title}
          </span>
        </div>
        {citation.page_number != null && (
          <span className="shrink-0 text-xs text-ink-500">p.{citation.page_number}</span>
        )}
      </div>
      <p className="mt-2 line-clamp-3 text-xs leading-snug text-ink-600">
        {citation.snippet}
      </p>
    </div>
  );
}
