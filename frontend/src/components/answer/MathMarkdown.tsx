// Loaded lazily: KaTeX (+ its CSS and fonts) is only fetched for answers containing math.
import "katex/dist/katex.min.css";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";

import type { MarkdownConfig, PluggableList } from "./markdownConfig";

// trust: false blocks \href/\includegraphics etc.; errors render inline instead of throwing.
const rehypePlugins: PluggableList = [
  [rehypeKatex, { throwOnError: false, strict: "ignore", trust: false, maxExpand: 500 }],
];

export default function MathMarkdown({
  content,
  remarkPlugins,
  components,
}: MarkdownConfig & { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkMath, ...remarkPlugins]}
      rehypePlugins={rehypePlugins}
      components={components}
    >
      {content}
    </ReactMarkdown>
  );
}
