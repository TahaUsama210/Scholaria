import type { Nodes, Parent, PhrasingContent, Root } from "mdast";

import type { ChatMessage } from "@/hooks/useChat";
import type { Citation } from "@/types/api";

/** A run of plain text, or a citation group like `[1]` / `[1, 3]`. */
export type CitationSegment = string | { markers: number[]; raw: string };

// `[1]`, `[2, 3]`, `[4,5,6]` — not `[^1]` footnotes, not `[a]`, at most 3 digits each.
const MARKER_GROUP = /\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]/g;

/**
 * Splits text into plain runs and citation groups. A group becomes a citation only when
 * every marker in it is known, so hallucinated markers like `[9]` stay visible as text.
 */
export function splitCitations(text: string, isKnown: (marker: number) => boolean): CitationSegment[] {
  const out: CitationSegment[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER_GROUP)) {
    const raw = match[0];
    const markers = (match[1] ?? "").split(",").map((s) => Number(s.trim()));
    if (!markers.every(isKnown)) continue;
    const start = match.index ?? 0;
    if (start > last) out.push(text.slice(last, start));
    out.push({ markers, raw });
    last = start + raw.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Distinct markers cited in `text`, in order of first appearance. */
export function citedMarkers(text: string): number[] {
  const seen = new Set<number>();
  for (const match of text.matchAll(MARKER_GROUP)) {
    for (const s of (match[1] ?? "").split(",")) seen.add(Number(s.trim()));
  }
  return [...seen];
}

// Never rewrite inside code, math, existing links or raw HTML.
const SKIP = new Set<Nodes["type"]>([
  "code",
  "inlineCode",
  "math",
  "inlineMath",
  "link",
  "linkReference",
  "definition",
  "html",
]);

/** Tag name the plugin emits; render it with a `cite` entry in react-markdown `components`. */
export const CITATION_TAG = "cite";

function citationNode(marker: number): PhrasingContent {
  return {
    type: "emphasis",
    data: { hName: CITATION_TAG, hProperties: { dataMarker: marker } },
    children: [{ type: "text", value: `[${marker}]` }],
  };
}

function transform(parent: Parent, isKnown: (marker: number) => boolean): void {
  const next: Nodes[] = [];
  let changed = false;
  for (const child of parent.children) {
    if (child.type === "text") {
      const segments = splitCitations(child.value, isKnown);
      if (segments.length === 1 && typeof segments[0] === "string") {
        next.push(child);
        continue;
      }
      changed = true;
      for (const seg of segments) {
        if (typeof seg === "string") next.push({ type: "text", value: seg });
        else next.push(...seg.markers.map(citationNode));
      }
      continue;
    }
    if ("children" in child && !SKIP.has(child.type)) transform(child, isKnown);
    next.push(child);
  }
  if (changed) parent.children = next as Parent["children"];
}

/** Remark plugin: turns known `[n]` markers in prose into `<cite data-marker="n">` nodes. */
export function remarkCitations(options: { isKnown: (marker: number) => boolean }) {
  return (tree: Root): void => transform(tree, options.isKnown);
}

export const sourceDomId = (messageId: string, marker: number): string =>
  `source-${messageId}-${marker}`;

/** "Source 2: Lecture 4, page 7" — used for chip labels and tooltips. */
export function sourceLabel(marker: number, source?: Citation): string {
  if (!source) return `Source ${marker}`;
  const page = source.page_number != null ? `, page ${source.page_number}` : "";
  return `Source ${marker}: ${source.document_title}${page}`;
}

/** Which markers the answer cites: the server's list, else parsed from the finished text. */
export function citedSet(message: ChatMessage): Set<number> | null {
  if (message.citations) return new Set(message.citations.map((c) => c.marker));
  if (message.status === "streaming") return null;
  return new Set(citedMarkers(message.content));
}

/** Answer text plus a plain-text source list, for pasting into notes. */
export function answerForClipboard(message: ChatMessage): string {
  const cited = message.citations ?? message.context;
  if (cited.length === 0) return message.content;
  const lines = cited.map(
    (c) => `[${c.marker}] ${c.document_title}${c.page_number != null ? `, p. ${c.page_number}` : ""}`,
  );
  return `${message.content}\n\nSources:\n${lines.join("\n")}`;
}
