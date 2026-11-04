import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, RATE_LIMIT_MESSAGE } from "@/lib/api";
import { streamChat } from "@/lib/sse";
import { makeCitation } from "@/test/utils";
import type { ChatRequest, StreamEvent } from "@/types/api";
import { buildHistory, useChat, type ChatMessage } from "../useChat";

vi.mock("@/lib/sse", () => ({ streamChat: vi.fn() }));
const mockStream = vi.mocked(streamChat);

/** Makes streamChat replay `events`, then resolve (or reject with `fail`). */
function replay(events: StreamEvent[], fail?: unknown) {
  mockStream.mockImplementationOnce(async (_body, { onEvent }) => {
    for (const ev of events) onEvent(ev);
    if (fail) throw fail;
  });
}

const answered = (text: string): StreamEvent[] => [
  { type: "context", data: [makeCitation(1), makeCitation(2)] },
  { type: "token", data: text.slice(0, 5) },
  { type: "token", data: text.slice(5) },
  { type: "citations", data: [makeCitation(1)] },
  { type: "done", data: { answer: text } },
];

const lastBody = (): ChatRequest => mockStream.mock.lastCall?.[0] as ChatRequest;
const assistantMsg = (messages: ChatMessage[]) => messages[messages.length - 1] as ChatMessage;

beforeEach(() => mockStream.mockReset());

describe("useChat", () => {
  it("builds the answer from context, token, citations and done events", async () => {
    replay(answered("Backprop uses the chain rule [1]."));
    const { result } = renderHook(() => useChat());

    await act(() => result.current.ask("  What is backprop?  ", { courseCode: "CS480" }));

    expect(lastBody()).toEqual({ question: "What is backprop?", course_code: "CS480", history: [] });
    const [user, asst] = result.current.messages;
    expect(user).toMatchObject({ role: "user", content: "What is backprop?", courseCode: "CS480" });
    expect(asst).toMatchObject({
      role: "assistant",
      status: "done",
      content: "Backprop uses the chain rule [1].",
    });
    expect(asst?.context.map((c) => c.marker)).toEqual([1, 2]);
    expect(asst?.citations?.map((c) => c.marker)).toEqual([1]);
    expect(result.current.isStreaming).toBe(false);
  });

  it("sends the last six completed turns as history", async () => {
    const { result } = renderHook(() => useChat());
    for (let i = 1; i <= 4; i += 1) {
      replay(answered(`Answer number ${i}`));
      await act(() => result.current.ask(`Question ${i}`));
    }
    replay(answered("Final answer"));
    await act(() => result.current.ask("And the follow-up?"));

    const { history } = lastBody();
    expect(history).toHaveLength(6);
    expect(history?.[0]).toEqual({ role: "user", content: "Question 2" });
    expect(history?.[5]).toEqual({ role: "assistant", content: "Answer number 4" });
  });

  it("shows server error events with their request id", async () => {
    replay([
      { type: "token", data: "Partial" },
      { type: "error", data: { message: "The language model is unavailable.", request_id: "req-42" } },
    ]);
    const { result } = renderHook(() => useChat());
    await act(() => result.current.ask("Q"));

    expect(assistantMsg(result.current.messages)).toMatchObject({
      status: "error",
      content: "Partial",
      error: { message: "The language model is unavailable.", requestId: "req-42" },
    });
  });

  it("maps a refused request (429) to a friendly message", async () => {
    replay([], new ApiError(RATE_LIMIT_MESSAGE, { status: 429, requestId: "rl-1" }));
    const { result } = renderHook(() => useChat());
    await act(() => result.current.ask("Q"));

    expect(assistantMsg(result.current.messages).error).toEqual({
      message: RATE_LIMIT_MESSAGE,
      requestId: "rl-1",
    });
  });

  it("never surfaces raw exception text from unexpected failures", async () => {
    replay([], new TypeError("Cannot read properties of undefined (reading 'x')"));
    const { result } = renderHook(() => useChat());
    await act(() => result.current.ask("Q"));

    const { error } = assistantMsg(result.current.messages);
    expect(error?.message).toMatch(/something went wrong/i);
    expect(error?.message).not.toMatch(/undefined/);
  });

  it("stops generation and keeps the partial answer", async () => {
    let signal: AbortSignal | undefined;
    mockStream.mockImplementationOnce((_body, opts) => {
      signal = opts.signal;
      opts.onEvent({ type: "token", data: "Half an answer" });
      return new Promise<void>((resolve) => opts.signal.addEventListener("abort", () => resolve()));
    });
    const { result } = renderHook(() => useChat());

    act(() => void result.current.ask("Q"));
    await waitFor(() => expect(result.current.isStreaming).toBe(true));
    act(() => result.current.stop());

    await waitFor(() => expect(assistantMsg(result.current.messages).status).toBe("stopped"));
    expect(signal?.aborted).toBe(true);
    expect(assistantMsg(result.current.messages).content).toBe("Half an answer");
  });

  it("retry replaces the failed exchange and re-asks with the same scope", async () => {
    replay([], new ApiError("Can't reach the server.", { status: 0 }));
    const { result } = renderHook(() => useChat());
    await act(() => result.current.ask("Q1", { courseCode: "CS348" }));

    replay(answered("Recovered [1]"));
    await act(async () => result.current.retry(assistantMsg(result.current.messages).id));

    await waitFor(() => expect(assistantMsg(result.current.messages).status).toBe("done"));
    expect(result.current.messages).toHaveLength(2);
    expect(lastBody()).toMatchObject({ question: "Q1", course_code: "CS348", history: [] });
  });

  it("reset clears the conversation", async () => {
    replay(answered("Hello there"));
    const { result } = renderHook(() => useChat());
    await act(() => result.current.ask("Hi"));
    act(() => result.current.reset());
    expect(result.current.messages).toEqual([]);
  });
});

describe("buildHistory", () => {
  const msg = (role: "user" | "assistant", content: string, status: ChatMessage["status"] = "done") =>
    ({ id: content, role, content, status, context: [], citations: null }) as ChatMessage;

  it("drops failed, stopped and empty assistant turns", () => {
    const history = buildHistory([
      msg("user", "q1"),
      msg("assistant", "partial", "error"),
      msg("user", "q2"),
      msg("assistant", "half", "stopped"),
      msg("user", "q3"),
      msg("assistant", "", "done"),
    ]);
    expect(history.map((h) => h.content)).toEqual(["q1", "q2", "q3"]);
  });
});
