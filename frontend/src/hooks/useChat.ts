import { useCallback, useRef, useState } from "react";
import { streamChat } from "@/lib/sse";
import type { Citation, StreamEvent } from "@/types/api";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  context?: Citation[];   // chunks the model was given (shown in citation rail)
  isStreaming?: boolean;
  error?: string;
}

function makeId(): string {
  return Math.random().toString(36).slice(2, 10);
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const ask = useCallback(
    async (question: string, courseCode?: string | null) => {
      if (!question.trim() || isStreaming) return;
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      const userMsg: ChatMessage = {
        id: makeId(),
        role: "user",
        content: question,
        citations: [],
      };
      const asstMsg: ChatMessage = {
        id: makeId(),
        role: "assistant",
        content: "",
        citations: [],
        context: [],
        isStreaming: true,
      };
      setMessages((prev) => [...prev, userMsg, asstMsg]);
      setIsStreaming(true);

      const patch = (next: Partial<ChatMessage>) =>
        setMessages((prev) =>
          prev.map((m) => (m.id === asstMsg.id ? { ...m, ...next } : m)),
        );

      try {
        await streamChat(
          { question, course_code: courseCode ?? null },
          {
            signal: ctrl.signal,
            onEvent(ev: StreamEvent) {
              switch (ev.type) {
                case "context":
                  patch({ context: ev.data });
                  break;
                case "token":
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === asstMsg.id ? { ...m, content: m.content + ev.data } : m,
                    ),
                  );
                  break;
                case "citations":
                  patch({ citations: ev.data });
                  break;
                case "done":
                  patch({ isStreaming: false });
                  break;
                case "error":
                  patch({ isStreaming: false, error: ev.data.message });
                  break;
              }
            },
            onError() {
              patch({ isStreaming: false, error: "Connection lost." });
            },
          },
        );
      } finally {
        setIsStreaming(false);
        abortRef.current = null;
      }
    },
    [isStreaming],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
    setMessages((prev) =>
      prev.map((m) => (m.isStreaming ? { ...m, isStreaming: false } : m)),
    );
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setIsStreaming(false);
  }, []);

  return { messages, isStreaming, ask, stop, reset };
}
