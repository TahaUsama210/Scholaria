import { fetchEventSource } from "@microsoft/fetch-event-source";
import type { ChatRequest, StreamEvent } from "@/types/api";

const BASE = import.meta.env.VITE_API_BASE_URL ?? "";

interface StreamOptions {
  signal: AbortSignal;
  onEvent(ev: StreamEvent): void;
  onError(): void;
}

export async function streamChat(
  body: ChatRequest,
  { signal, onEvent, onError }: StreamOptions,
): Promise<void> {
  try {
    await fetchEventSource(`${BASE}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
      async onopen(res) {
        if (!res.ok) {
          onError();
          throw new Error(`HTTP ${res.status}`);
        }
      },
      onmessage(msg) {
        if (!msg.event || !msg.data) return;
        try {
          const data: unknown = JSON.parse(msg.data);
          onEvent({ type: msg.event, data } as StreamEvent);
        } catch {
          // ignore malformed event data
        }
      },
      onerror(err: unknown) {
        if (signal.aborted) return;
        onError();
        throw err;
      },
      openWhenHidden: true,
    });
  } catch {
    // onerror callback has already updated the UI; suppress propagation
  }
}
