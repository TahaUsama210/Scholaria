import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import { streamChat } from "@/lib/sse";
import type { ChatRole, ChatTurn, Citation, StreamEvent } from "@/types/api";

export type MessageStatus = "streaming" | "done" | "error" | "stopped";

export interface ChatError {
  message: string;
  requestId: string | null;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  status: MessageStatus;
  /** Course scope the question was asked with (user messages). */
  courseCode?: string | null;
  /** Chunks retrieved for this answer (`context` event). */
  context: Citation[];
  /** Chunks the answer actually cited (`citations` event); null until it arrives. */
  citations: Citation[] | null;
  error?: ChatError;
}

/** Prior turns sent with each question so the backend can resolve follow-ups. */
export const HISTORY_LIMIT = 6;

const UNEXPECTED_ERROR: ChatError = {
  message: "Something went wrong while generating the answer. Please try again.",
  requestId: null,
};

let nextId = 0;
function makeId(prefix: string): string {
  nextId += 1;
  return `${prefix}-${Date.now().toString(36)}-${nextId}`;
}

/** Completed turns only: failed or empty assistant replies would confuse condensation. */
export function buildHistory(messages: readonly ChatMessage[], limit = HISTORY_LIMIT): ChatTurn[] {
  return messages
    .filter((m) => m.content.trim() && (m.role === "user" || m.status === "done"))
    .slice(-limit)
    .map(({ role, content }) => ({ role, content }));
}

function toChatError(err: unknown): ChatError {
  if (err instanceof ApiError) return { message: err.message, requestId: err.requestId };
  return UNEXPECTED_ERROR;
}

const schedule: (cb: () => void) => void =
  typeof window !== "undefined" && typeof window.requestAnimationFrame === "function"
    ? (cb) => window.requestAnimationFrame(cb)
    : (cb) => setTimeout(cb, 16);

export interface AskOptions {
  courseCode?: string | null;
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const abortRef = useRef<AbortController | null>(null);

  // Tokens can arrive far faster than the screen refreshes; re-rendering markdown + KaTeX
  // per token is wasteful, so buffer them and flush once per animation frame.
  const pending = useRef<{ id: string; text: string } | null>(null);
  const flushScheduled = useRef(false);

  const flushTokens = useCallback(() => {
    flushScheduled.current = false;
    const buf = pending.current;
    if (!buf || !buf.text) return;
    const { id, text } = buf;
    buf.text = "";
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, content: m.content + text } : m)));
  }, []);

  const patch = useCallback((id: string, next: Partial<ChatMessage>) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...next } : m)));
  }, []);

  const isStreaming = messages.some((m) => m.status === "streaming");

  const ask = useCallback(
    async (rawQuestion: string, { courseCode = null }: AskOptions = {}) => {
      const question = rawQuestion.trim();
      if (!question || abortRef.current) return;
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      const history = buildHistory(messagesRef.current);
      const userMsg: ChatMessage = {
        id: makeId("u"),
        role: "user",
        content: question,
        status: "done",
        courseCode,
        context: [],
        citations: null,
      };
      const asstId = makeId("a");
      const asstMsg: ChatMessage = {
        id: asstId,
        role: "assistant",
        content: "",
        status: "streaming",
        context: [],
        citations: null,
      };
      setMessages((prev) => [...prev, userMsg, asstMsg]);
      pending.current = { id: asstId, text: "" };

      const onEvent = (ev: StreamEvent) => {
        switch (ev.type) {
          case "context":
            patch(asstId, { context: ev.data });
            break;
          case "token":
            if (pending.current?.id !== asstId) return;
            pending.current.text += ev.data;
            if (!flushScheduled.current) {
              flushScheduled.current = true;
              schedule(flushTokens);
            }
            break;
          case "citations":
            patch(asstId, { citations: ev.data });
            break;
          case "done":
            flushTokens();
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstId
                  ? { ...m, status: "done", content: m.content || ev.data.answer || "" }
                  : m,
              ),
            );
            break;
          case "error":
            flushTokens();
            patch(asstId, {
              status: "error",
              error: { message: ev.data.message, requestId: ev.data.request_id ?? null },
            });
            break;
        }
      };

      try {
        await streamChat(
          { question, course_code: courseCode, history },
          { signal: ctrl.signal, onEvent },
        );
        flushTokens();
        // Aborted (stop / new chat) or the stream ended cleanly; settle anything left open.
        setMessages((prev) =>
          prev.map((m) =>
            m.id === asstId && m.status === "streaming"
              ? { ...m, status: ctrl.signal.aborted ? "stopped" : "done" }
              : m,
          ),
        );
      } catch (err) {
        flushTokens();
        patch(asstId, { status: "error", error: toChatError(err) });
      } finally {
        if (abortRef.current === ctrl) abortRef.current = null;
        if (pending.current?.id === asstId) pending.current = null;
      }
    },
    [flushTokens, patch],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    flushTokens();
    setMessages((prev) =>
      prev.map((m) => (m.status === "streaming" ? { ...m, status: "stopped" } : m)),
    );
  }, [flushTokens]);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    pending.current = null;
    setMessages([]);
  }, []);

  /** Re-asks the question behind a failed or stopped answer, replacing that exchange. */
  const retry = useCallback(
    (assistantId: string) => {
      const list = messagesRef.current;
      const idx = list.findIndex((m) => m.id === assistantId);
      const question = idx > 0 ? list[idx - 1] : undefined;
      if (!question || question.role !== "user" || abortRef.current) return;
      const trimmed = list.filter((_, i) => i !== idx && i !== idx - 1);
      messagesRef.current = trimmed;
      setMessages(trimmed);
      void ask(question.content, { courseCode: question.courseCode ?? null });
    },
    [ask],
  );

  // Abort an in-flight stream when the chat unmounts.
  useEffect(() => () => abortRef.current?.abort(), []);

  return { messages, isStreaming, ask, stop, reset, retry };
}
