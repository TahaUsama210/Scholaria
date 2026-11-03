import type { Components, Options } from "react-markdown";
import remarkGfm from "remark-gfm";

import { remarkCitations } from "@/lib/citations";
import type { Citation } from "@/types/api";
import { CitationChip } from "./CitationChip";

export type PluggableList = NonNullable<Options["remarkPlugins"]>;

export interface AnswerRenderProps {
  content: string;
  /** Sources the answer may cite; only these markers become chips. */
  sources: readonly Citation[];
  activeMarker?: number | null;
  onCite?(marker: number): void;
}

export interface MarkdownConfig {
  remarkPlugins: PluggableList;
  components: Components;
}

export function buildMarkdownConfig({
  sources,
  activeMarker,
  onCite,
}: Omit<AnswerRenderProps, "content">): MarkdownConfig {
  const byMarker = new Map(sources.map((s) => [s.marker, s]));
  return {
    remarkPlugins: [remarkGfm, [remarkCitations, { isKnown: (n: number) => byMarker.has(n) }]],
    components: {
      cite: ({ node }) => {
        const marker = Number(node?.properties?.dataMarker);
        return (
          <CitationChip
            marker={marker}
            source={byMarker.get(marker)}
            active={marker === activeMarker}
            onSelect={onCite}
          />
        );
      },
      // Model output is untrusted: links open in a new tab without referrer/opener, and
      // images are reduced to their alt text so answers can't load remote content.
      a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
      img: ({ alt }) => (alt ? <span>{alt}</span> : null),
    },
  };
}
