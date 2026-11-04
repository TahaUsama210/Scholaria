import { useEffect, useId, useMemo } from "react";

import type { ChatMessage } from "@/hooks/useChat";
import { citedSet, sourceDomId } from "@/lib/citations";
import { pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { CitationCard } from "./CitationCard";

interface Props {
  message: ChatMessage;
  activeMarker: number | null;
  /** Changes on every chip click so re-selecting the same source scrolls to it again. */
  focusNonce?: number;
  className?: string;
}

export function SourcesPanel({ message, activeMarker, focusNonce = 0, className }: Props) {
  const headingId = useId();
  const sources = message.context.length > 0 ? message.context : (message.citations ?? []);
  const cited = useMemo(() => citedSet(message), [message]);

  useEffect(() => {
    if (activeMarker == null) return;
    const el = document.getElementById(sourceDomId(message.id, activeMarker));
    if (!el) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView?.({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
    el.focus({ preventScroll: true });
  }, [message.id, activeMarker, focusNonce]);

  const summary =
    sources.length === 0
      ? null
      : cited
        ? `${pluralize(sources.length, "source")} · ${cited.size} cited`
        : pluralize(sources.length, "source");

  return (
    <section aria-labelledby={headingId} className={cn("space-y-2", className)}>
      <div className="flex items-baseline justify-between gap-2 px-1">
        <h2 id={headingId} className="section-title">
          Sources
        </h2>
        {summary && <span className="text-xs text-ink-500">{summary}</span>}
      </div>
      {sources.length === 0 ? (
        <p className="px-1 text-xs text-ink-500">
          {message.status === "streaming"
            ? "Searching your library…"
            : "No matching passages were found for this question."}
        </p>
      ) : (
        <ol className="space-y-2" aria-label="Retrieved passages">
          {sources.map((c) => (
            <li key={c.chunk_id}>
              <CitationCard
                id={sourceDomId(message.id, c.marker)}
                citation={c}
                cited={cited ? cited.has(c.marker) : null}
                active={c.marker === activeMarker}
              />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
