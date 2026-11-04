import { memo, useCallback, useEffect, useId, useState } from "react";
import { Check, ChevronDown, Copy, GraduationCap, Loader2, RotateCcw } from "lucide-react";

import type { ChatMessage } from "@/hooks/useChat";
import { useCopy } from "@/hooks/useCopy";
import { answerForClipboard } from "@/lib/citations";
import { pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { AnswerMarkdown } from "./answer/AnswerMarkdown";
import { ErrorNotice } from "./ErrorNotice";
import { SourcesPanel } from "./SourcesPanel";

interface Props {
  message: ChatMessage;
  /** Marker selected via a chip in this message (null if another message is focused). */
  activeMarker: number | null;
  focusNonce: number;
  /** Render sources under the answer (narrow layouts) instead of in the side rail. */
  inlineSources: boolean;
  onCite(messageId: string, marker: number): void;
  onRetry(messageId: string): void;
}

export const MessageBubble = memo(function MessageBubble(props: Props) {
  return props.message.role === "user" ? (
    <UserMessage message={props.message} />
  ) : (
    <AssistantMessage {...props} />
  );
});

function UserMessage({ message }: { message: ChatMessage }) {
  return (
    <div className="flex justify-end" data-role="user">
      <div className="max-w-[85%] sm:max-w-[75%]">
        <p className="whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-[0.9375rem] leading-relaxed text-accent-fg shadow-sm">
          {message.content}
        </p>
        {message.courseCode && (
          <p className="mt-1 text-right text-xs text-ink-500">in {message.courseCode}</p>
        )}
      </div>
    </div>
  );
}

function AssistantMessage({
  message,
  activeMarker,
  focusNonce,
  inlineSources,
  onCite,
  onRetry,
}: Props) {
  const { id, status, content, context } = message;
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const sourcesId = useId();
  const { state: copyState, copy } = useCopy();
  const streaming = status === "streaming";
  const sourceCount = context.length || message.citations?.length || 0;

  const handleCite = useCallback((marker: number) => onCite(id, marker), [onCite, id]);
  const handleRetry = useCallback(() => onRetry(id), [onRetry, id]);

  useEffect(() => {
    if (activeMarker != null) setSourcesOpen(true);
  }, [activeMarker, focusNonce]);

  return (
    <div className="flex gap-3" data-role="assistant" data-status={status}>
      <div
        aria-hidden="true"
        className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/10 text-accent-text"
      >
        <GraduationCap className="h-4 w-4" />
      </div>

      <div className="min-w-0 flex-1">
        {streaming && !content && (
          <p className="flex items-center gap-2 py-1 text-sm text-ink-500">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            <span className="animate-pulse-soft">
              {context.length > 0
                ? `Reading ${pluralize(context.length, "source")}…`
                : "Searching your materials…"}
            </span>
          </p>
        )}

        {content && (
          <AnswerMarkdown
            content={content}
            sources={context}
            activeMarker={activeMarker}
            onCite={handleCite}
          />
        )}
        {streaming && content && (
          <span
            aria-hidden="true"
            className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse-soft rounded-sm bg-accent/70"
          />
        )}

        {status === "error" && message.error && (
          <ErrorNotice
            className={cn(content && "mt-3")}
            message={message.error.message}
            requestId={message.error.requestId}
            onRetry={handleRetry}
          />
        )}

        {status === "stopped" && (
          <p className="mt-2 flex items-center gap-2 text-xs text-ink-500">
            Generation stopped.
            <button
              type="button"
              onClick={handleRetry}
              className="inline-flex items-center gap-1 rounded font-medium text-accent-text hover:underline"
            >
              <RotateCcw className="h-3 w-3" aria-hidden="true" />
              Regenerate
            </button>
          </p>
        )}

        {!streaming && (content || sourceCount > 0) && (
          <div className="mt-2 flex flex-wrap items-center gap-1">
            {content && (
              <button
                type="button"
                onClick={() => void copy(answerForClipboard(message))}
                className="btn-ghost px-2 py-1 text-xs"
              >
                {copyState === "copied" ? (
                  <Check className="h-3.5 w-3.5 text-emerald-600" aria-hidden="true" />
                ) : (
                  <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                <span aria-live="polite">
                  {copyState === "copied" ? "Copied" : copyState === "failed" ? "Copy failed" : "Copy"}
                </span>
              </button>
            )}
            {inlineSources && sourceCount > 0 && (
              <button
                type="button"
                onClick={() => setSourcesOpen((v) => !v)}
                aria-expanded={sourcesOpen}
                aria-controls={sourcesId}
                className="btn-ghost px-2 py-1 text-xs"
              >
                {pluralize(sourceCount, "source")}
                <ChevronDown
                  className={cn("h-3.5 w-3.5 transition-transform", sourcesOpen && "rotate-180")}
                  aria-hidden="true"
                />
              </button>
            )}
          </div>
        )}

        {inlineSources && sourcesOpen && (
          <div id={sourcesId} className="mt-3">
            <SourcesPanel message={message} activeMarker={activeMarker} focusNonce={focusNonce} />
          </div>
        )}
      </div>
    </div>
  );
}
