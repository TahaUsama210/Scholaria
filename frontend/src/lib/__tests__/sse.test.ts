import { afterEach, describe, expect, it, vi } from "vitest";

import type { StreamEvent } from "@/types/api";
import { ApiError, NETWORK_ERROR_MESSAGE, RATE_LIMIT_MESSAGE } from "../api";
import { INTERRUPTED_MESSAGE, parseStreamEvent, streamChat } from "../sse";

function sseResponse(frames: string, headers: Record<string, string> = {}): Response {
  return new Response(frames, {
    status: 200,
    headers: { "content-type": "text/event-stream", "x-request-id": "rid-1", ...headers },
  });
}

async function run(response: Response | Error) {
  const fetchMock = vi.fn(() =>
    response instanceof Error ? Promise.reject(response) : Promise.resolve(response),
  );
  vi.stubGlobal("fetch", fetchMock);
  const events: StreamEvent[] = [];
  const outcome = await streamChat(
    { question: "q", course_code: null, history: [] },
    { signal: new AbortController().signal, onEvent: (e) => events.push(e) },
  ).then(
    () => null,
    (err: unknown) => err,
  );
  return { events, error: outcome, fetchMock };
}

afterEach(() => vi.unstubAllGlobals());

describe("parseStreamEvent", () => {
  it("parses known events and ignores keep-alives, unknown names and bad JSON", () => {
    expect(parseStreamEvent("token", '"hi"')).toEqual({ type: "token", data: "hi" });
    expect(parseStreamEvent("", "")).toBeNull();
    expect(parseStreamEvent("ping", "{}")).toBeNull();
    expect(parseStreamEvent("token", "{not json")).toBeNull();
  });
});

describe("streamChat", () => {
  it("posts to /api/chat/stream and dispatches events until done", async () => {
    const { events, error, fetchMock } = await run(
      sseResponse('event: token\ndata: "Hel"\n\nevent: token\ndata: "lo"\n\nevent: done\ndata: {"answer":"Hello"}\n\n'),
    );
    expect(error).toBeNull();
    expect(events.map((e) => e.type)).toEqual(["token", "token", "done"]);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/chat/stream");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ question: "q", course_code: null, history: [] });
  });

  it("rejects a 429 with the rate-limit message and request id", async () => {
    const { error } = await run(
      new Response(JSON.stringify({ detail: "Rate limit exceeded: 20 per 1 minute" }), {
        status: 429,
        headers: { "content-type": "application/json", "x-request-id": "rid-429" },
      }),
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 429, message: RATE_LIMIT_MESSAGE, requestId: "rid-429" });
  });

  it("does not leak server error details for 5xx", async () => {
    const { error } = await run(
      new Response(JSON.stringify({ detail: "psycopg.OperationalError: connection refused" }), {
        status: 500,
      }),
    );
    expect((error as ApiError).message).not.toMatch(/psycopg/);
  });

  it("reports a stream that ends before done as interrupted (no retry)", async () => {
    const { error, fetchMock } = await run(sseResponse('event: token\ndata: "Hel"\n\n'));
    expect(error).toMatchObject({ message: INTERRUPTED_MESSAGE, requestId: "rid-1" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reports network failures as unreachable", async () => {
    const { error } = await run(new TypeError("Failed to fetch"));
    expect(error).toMatchObject({ status: 0, message: NETWORK_ERROR_MESSAGE });
  });

  it("rejects non-SSE success responses (e.g. an HTML fallback page)", async () => {
    const { error } = await run(
      new Response("<html></html>", { status: 200, headers: { "content-type": "text/html" } }),
    );
    expect((error as ApiError).message).toMatch(/unexpected response/i);
  });
});
