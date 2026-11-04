import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, RATE_LIMIT_MESSAGE } from "@/lib/api";
import { streamChat } from "@/lib/sse";
import { makeCitation } from "@/test/utils";
import type { ChatRequest, StreamEvent } from "@/types/api";
import { ChatWindow } from "../ChatWindow";

vi.mock("@/lib/sse", () => ({ streamChat: vi.fn() }));
const mockStream = vi.mocked(streamChat);

function replay(events: StreamEvent[], fail?: unknown) {
  mockStream.mockImplementationOnce(async (_body, { onEvent }) => {
    for (const ev of events) onEvent(ev);
    if (fail) throw fail;
  });
}

const renderChat = (courseCode: string | null = null) =>
  render(<ChatWindow courseCode={courseCode} libraryEmpty={false} onRequestUpload={() => undefined} />);

const textbox = () => screen.getByRole("textbox", { name: "Ask a question" });

beforeEach(() => mockStream.mockReset());

describe("ChatWindow", () => {
  it("sends on Enter, inserts a newline on Shift+Enter", async () => {
    replay([{ type: "done", data: { answer: "" } }]);
    renderChat("CS350");
    const u = userEvent.setup();

    await u.type(textbox(), "line one{Shift>}{Enter}{/Shift}line two");
    expect(textbox()).toHaveValue("line one\nline two");
    await u.keyboard("{Enter}");

    await waitFor(() => expect(mockStream).toHaveBeenCalledTimes(1));
    expect((mockStream.mock.calls[0]?.[0] as ChatRequest).question).toBe("line one\nline two");
    expect((mockStream.mock.calls[0]?.[0] as ChatRequest).course_code).toBe("CS350");
    expect(textbox()).toHaveValue("");
  });

  it("asks an example question when clicked", async () => {
    replay([{ type: "done", data: { answer: "" } }]);
    renderChat();
    await userEvent.click(screen.getByRole("button", { name: /2NF and 3NF/ }));
    await waitFor(() => expect(mockStream).toHaveBeenCalledTimes(1));
    expect(screen.getByText("What is the difference between 2NF and 3NF?")).toBeInTheDocument();
  });

  it("renders error events with message, request id and retry", async () => {
    replay([{ type: "error", data: { message: "Answer generation failed.", request_id: "req-9f2" } }]);
    renderChat();
    const u = userEvent.setup();
    await u.type(textbox(), "Why?{Enter}");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Answer generation failed.");
    expect(alert).toHaveTextContent("Request ID: req-9f2");

    replay([
      { type: "context", data: [makeCitation(1)] },
      { type: "token", data: "Because [1]." },
      { type: "done", data: { answer: "Because [1]." } },
    ]);
    await u.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("button", { name: /show source 1/i })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders a friendly rate-limit message", async () => {
    replay([], new ApiError(RATE_LIMIT_MESSAGE, { status: 429, requestId: "rl-7" }));
    renderChat();
    await userEvent.type(textbox(), "Q{Enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(RATE_LIMIT_MESSAGE);
    expect(alert).toHaveTextContent("rl-7");
  });

  it("clicking a citation chip reveals and highlights its source card", async () => {
    replay([
      { type: "context", data: [makeCitation(1), makeCitation(2, { document_title: "DB Notes" })] },
      { type: "token", data: "Normal forms reduce redundancy [2]." },
      { type: "citations", data: [makeCitation(2, { document_title: "DB Notes" })] },
      { type: "done", data: { answer: "Normal forms reduce redundancy [2]." } },
    ]);
    renderChat();
    const u = userEvent.setup();
    await u.type(textbox(), "What is 3NF?{Enter}");

    await u.click(await screen.findByRole("button", { name: "Show source 2: DB Notes, page 3" }));
    const card = screen.getByRole("article", { name: "Source 2: DB Notes" });
    expect(card).toHaveAttribute("aria-current", "true");
    expect(card).toHaveFocus();
    expect(within(card).getByText("Cited")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: /Source 1/ })).not.toHaveAttribute("aria-current");
  });

  it("New chat clears the conversation", async () => {
    replay([{ type: "token", data: "Hi" }, { type: "done", data: { answer: "Hi" } }]);
    renderChat();
    const u = userEvent.setup();
    await u.type(textbox(), "Hello{Enter}");
    await screen.findByText("Hi");
    await u.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.queryByText("Hi")).not.toBeInTheDocument();
    expect(screen.getByText(/Ask anything about your course materials/)).toBeInTheDocument();
  });
});
