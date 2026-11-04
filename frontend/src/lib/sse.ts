import { fetchEventSource } from "@microsoft/fetch-event-source";

import type { ChatRequest, StreamEvent } from "@/types/api";
import { ApiError, apiUrl, errorFromResponse, NETWORK_ERROR_MESSAGE } from "./api";

export const INTERRUPTED_MESSAGE =
  "The connection was interrupted before the answer finished. Please try again.";

interface StreamOptions {
  signal: AbortSignal;
  onEvent(ev: StreamEvent): void;
}

const EVENT_TYPES = new Set<StreamEvent["type"]>(["context", "token", "citations", "done", "error"]);

/** Parses one SSE frame; returns null for keep-alives, unknown events or malformed JSON. */
export function parseStreamEvent(event: string, data: string): StreamEvent | null {
  if (!EVENT_TYPES.has(event as StreamEvent["type"]) || !data) return null;
  try {
    return { type: event, data: JSON.parse(data) as unknown } as StreamEvent;
  } catch {
    return null;
  }
}

/**
 * POSTs a question and dispatches SSE events until the server sends `done` or `error`.
 *
 * Resolves when the stream finishes or `signal` aborts. Rejects with an ApiError when the
 * request is refused (non-2xx, e.g. 429), the network fails, or the stream ends early.
 * Never retries: replaying a POST would generate (and bill) a second answer.
 */
export async function streamChat(body: ChatRequest, { signal, onEvent }: StreamOptions): Promise<void> {
  let finished = false;
  let requestId: string | null = null;
  const alreadyDone = new Error("stream already finished");

  try {
    await fetchEventSource(apiUrl("/chat/stream"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
      openWhenHidden: true,
      async onopen(res) {
        requestId = res.headers.get("x-request-id");
        if (!res.ok) throw await errorFromResponse(res);
        if (!res.headers.get("content-type")?.includes("text/event-stream")) {
          throw new ApiError("Unexpected response from the server.", {
            status: res.status,
            requestId,
          });
        }
      },
      onmessage(msg) {
        const ev = parseStreamEvent(msg.event, msg.data);
        if (!ev) return;
        if (ev.type === "done" || ev.type === "error") finished = true;
        onEvent(ev);
      },
      onclose() {
        if (!finished) throw new ApiError(INTERRUPTED_MESSAGE, { status: 0, requestId });
      },
      onerror(err: unknown) {
        // Every branch throws: returning would trigger fetch-event-source's retry loop.
        if (finished) throw alreadyDone;
        if (err instanceof ApiError) throw err;
        throw new ApiError(requestId ? INTERRUPTED_MESSAGE : NETWORK_ERROR_MESSAGE, {
          status: 0,
          requestId,
        });
      },
    });
  } catch (err) {
    if (err === alreadyDone) return; // socket dropped after `done`/`error`: nothing lost
    throw err;
  }
}
