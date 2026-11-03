import { useCallback, useRef, useState } from "react";
import { ArrowDown, Filter, SquarePen } from "lucide-react";

import { useAutoScroll } from "@/hooks/useAutoScroll";
import { useChat, type ChatMessage } from "@/hooks/useChat";
import { BREAKPOINTS, useMediaQuery } from "@/hooks/useMediaQuery";
import { Composer } from "./Composer";
import { EmptyState } from "./EmptyState";
import { MessageBubble } from "./MessageBubble";
import { SourcesPanel } from "./SourcesPanel";

interface Props {
  courseCode: string | null;
  libraryEmpty: boolean;
  onRequestUpload(): void;
}

interface ActiveSource {
  messageId: string;
  marker: number;
  nonce: number;
}

function lastAssistant(messages: readonly ChatMessage[]): ChatMessage | undefined {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m?.role === "assistant") return m;
  }
  return undefined;
}

function statusAnnouncement(message: ChatMessage | undefined): string {
  switch (message?.status) {
    case "streaming":
      return "Generating answer…";
    case "done":
      return "Answer ready.";
    case "stopped":
      return "Generation stopped.";
    default:
      return ""; // errors are announced by the alert itself
  }
}

export function ChatWindow({ courseCode, libraryEmpty, onRequestUpload }: Props) {
  const { messages, isStreaming, ask, stop, reset, retry } = useChat();
  const wide = useMediaQuery(BREAKPOINTS.xl);
  const [active, setActive] = useState<ActiveSource | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const { ref: scrollRef, onScroll, atBottom, pin, scrollToBottom } =
    useAutoScroll<HTMLDivElement>(messages);

  const latest = lastAssistant(messages);
  const railMessage = (active && messages.find((m) => m.id === active.messageId)) || latest;

  const handleAsk = useCallback(
    (question: string) => {
      setActive(null);
      pin();
      void ask(question, { courseCode });
    },
    [ask, courseCode, pin],
  );

  const handleCite = useCallback((messageId: string, marker: number) => {
    setActive((prev) => ({ messageId, marker, nonce: (prev?.nonce ?? 0) + 1 }));
  }, []);

  const handleRetry = useCallback(
    (messageId: string) => {
      setActive(null);
      pin();
      retry(messageId);
    },
    [retry, pin],
  );

  const newChat = () => {
    reset();
    setActive(null);
    composerRef.current?.focus();
  };

  return (
    <div className="flex h-full min-h-0 gap-4">
      <section
        aria-label="Chat"
        className="card relative flex min-w-0 flex-1 flex-col overflow-hidden"
      >
        <div className="flex items-center justify-between gap-2 border-b border-ink-200 px-3 py-2 sm:px-4">
          <p className="flex min-w-0 items-center gap-1.5 text-sm text-ink-600">
            <Filter className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="truncate">
              {courseCode ? (
                <>
                  Answering from <span className="font-medium text-ink-900">{courseCode}</span>
                </>
              ) : (
                "Answering from all courses"
              )}
            </span>
          </p>
          <button
            type="button"
            onClick={newChat}
            disabled={messages.length === 0}
            className="btn-ghost shrink-0 px-2.5 py-1.5"
          >
            <SquarePen className="h-4 w-4" aria-hidden="true" />
            New chat
          </button>
        </div>

        <div className="relative min-h-0 flex-1">
          <div
            ref={scrollRef}
            onScroll={onScroll}
            tabIndex={0}
            role="region"
            aria-label="Conversation"
            className="h-full overflow-y-auto overscroll-contain focus-visible:ring-inset focus-visible:ring-offset-0"
          >
            {messages.length === 0 ? (
              <div className="h-full px-4">
                <EmptyState libraryEmpty={libraryEmpty} onAsk={handleAsk} onUpload={onRequestUpload} />
              </div>
            ) : (
              <div className="mx-auto max-w-3xl space-y-6 px-3 py-6 sm:px-5">
                {messages.map((m) => (
                  <MessageBubble
                    key={m.id}
                    message={m}
                    activeMarker={active?.messageId === m.id ? active.marker : null}
                    focusNonce={active?.messageId === m.id ? active.nonce : 0}
                    inlineSources={!wide}
                    onCite={handleCite}
                    onRetry={handleRetry}
                  />
                ))}
              </div>
            )}
          </div>

          {!atBottom && messages.length > 0 && (
            <button
              type="button"
              onClick={scrollToBottom}
              aria-label="Jump to latest"
              title="Jump to latest"
              className="btn-secondary absolute bottom-3 left-1/2 h-9 w-9 -translate-x-1/2 rounded-full p-0 shadow-md"
            >
              <ArrowDown className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
        </div>

        <Composer
          ref={composerRef}
          isStreaming={isStreaming}
          courseCode={courseCode}
          onSubmit={handleAsk}
          onStop={stop}
        />

        <p role="status" className="sr-only">
          {statusAnnouncement(latest)}
        </p>
      </section>

      {wide && (
        <aside aria-label="Sources for the selected answer" className="w-80 shrink-0 overflow-y-auto pb-2">
          {railMessage ? (
            <SourcesPanel
              message={railMessage}
              activeMarker={active?.messageId === railMessage.id ? active.marker : null}
              focusNonce={active?.nonce ?? 0}
            />
          ) : (
            <div className="space-y-2 px-1">
              <h2 className="section-title">Sources</h2>
              <p className="text-sm text-ink-500">
                The passages behind each answer appear here. Click a numbered marker in an answer
                to jump to its source.
              </p>
            </div>
          )}
        </aside>
      )}
    </div>
  );
}
