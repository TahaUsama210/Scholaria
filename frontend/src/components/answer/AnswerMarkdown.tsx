import { lazy, memo, Suspense } from "react";
import ReactMarkdown from "react-markdown";

import { buildMarkdownConfig, type AnswerRenderProps } from "./markdownConfig";

const MathMarkdown = lazy(() => import("./MathMarkdown"));

/** `$...$` / `$$...$$` (remark-math syntax) — cheap check before paying for KaTeX. */
const mayContainMath =(text: string): boolean => text.includes("$");

/**
 * Renders a model answer as markdown with clickable citation chips. Raw HTML is never
 * rendered (react-markdown default) and URLs pass through its default sanitiser.
 */
export const AnswerMarkdown = memo(function AnswerMarkdown(props: AnswerRenderProps) {
  const config = buildMarkdownConfig(props);
  const plain = (
    <ReactMarkdown remarkPlugins={config.remarkPlugins} components={config.components}>
      {props.content}
    </ReactMarkdown>
  );
  return (
    <div className="prose-answer">
      {mayContainMath(props.content) ? (
        <Suspense fallback={plain}>
          <MathMarkdown content={props.content} {...config} />
        </Suspense>
      ) : (
        plain
      )}
    </div>
  );
});
