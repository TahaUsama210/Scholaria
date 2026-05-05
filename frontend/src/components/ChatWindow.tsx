import { useEffect, useRef, useState, type FormEvent } from "react";
import { Send, Square } from "lucide-react";

import { useChat } from "@/hooks/useChat";
import { MessageBubble } from "./MessageBubble";
import { CitationCard } from "./CitationCard";

export function ChatWindow({ courseCode }: { courseCode?: string | null }) {
  const { messages, isStreaming, ask, stop } = useChat();
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
    void ask(input, courseCode);
    setInput("");
  };

  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const citationRail = lastAssistant?.context ?? lastAssistant?.citations ?? [];
  const cited = new Set(lastAssistant?.citations.map((c) => c.marker) ?? []);

  return (
    <div className="flex h-full flex-col gap-4 lg:flex-row">
      {/* Conversation */}
      <div className="flex flex-1 flex-col card overflow-hidden">
        <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-4">
          {messages.length === 0 ? (
            <Empty />
          ) : (
            messages.map((m) => <MessageBubble key={m.id} message={m} />)
          )}
        </div>
        <form
          onSubmit={onSubmit}
          className="flex items-center gap-2 border-t border-ink-200 bg-ink-50 p-3"
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about your course materials…"
            className="flex-1 rounded-md border border-ink-200 bg-white px-3 py-2 text-sm
                       placeholder:text-ink-400 focus-visible:outline-none
                       focus-visible:ring-2 focus-visible:ring-accent"
            disabled={isStreaming}
          />
          {isStreaming ? (
            <button type="button" className="btn-ghost" onClick={stop}>
              <Square className="mr-1 h-4 w-4" />
              Stop
            </button>
          ) : (
            <button type="submit" className="btn-primary" disabled={!input.trim()}>
              <Send className="mr-1 h-4 w-4" />
              Send
            </button>
          )}
        </form>
      </div>

      {/* Citation rail */}
      <aside className="lg:w-80 shrink-0 space-y-2 overflow-y-auto">
        <h2 className="px-1 text-xs font-semibold uppercase tracking-wide text-ink-500">
          Sources
        </h2>
        {citationRail.length === 0 ? (
          <p className="px-1 text-xs text-ink-400">
            Sources used by the model will appear here once you ask a question.
          </p>
        ) : (
          citationRail.map((c) => (
            <CitationCard key={c.chunk_id} citation={c} highlighted={cited.has(c.marker)} />
          ))
        )}
      </aside>
    </div>
  );
}

function Empty() {
  const examples = [
    "Explain backpropagation with the chain rule.",
    "What's the difference between 2NF and 3NF?",
    "Summarise the bias–variance tradeoff.",
  ];
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
      <div>
        <h3 className="text-lg font-semibold text-ink-800">
          Ask a question about your course materials
        </h3>
        <p className="mt-1 text-sm text-ink-500">
          Answers stream in token-by-token with citations to the source PDF and page.
        </p>
      </div>
      <ul className="space-y-1 text-sm text-ink-500">
        {examples.map((e) => (
          <li key={e} className="font-mono text-xs">
            “{e}”
          </li>
        ))}
      </ul>
    </div>
  );
}
