import type { AgentEventData, EventType } from "@/types/events";

export interface RawSSEFrame {
  id: string | null;
  event: string | null;
  data: string | null;
}

/** Split a raw SSE byte stream into frames (blocks separated by a blank
 * line) and parse each frame's `id:`/`event:`/`data:` fields. A bare
 * comment line (starting with ':', e.g. the backend's keep-alive) parses
 * to a frame with event=null, data=null -- callers should ignore those. */
export function parseSSEFrame(frameText: string): RawSSEFrame {
  const frame: RawSSEFrame = { id: null, event: null, data: null };
  for (const line of frameText.split("\n")) {
    if (line.startsWith(":") || line.trim() === "") continue;
    const separatorIndex = line.indexOf(":");
    if (separatorIndex === -1) continue;
    const field = line.slice(0, separatorIndex);
    const value = line.slice(separatorIndex + 1).replace(/^ /, "");
    if (field === "id") frame.id = value;
    else if (field === "event") frame.event = value;
    else if (field === "data") frame.data = frame.data === null ? value : `${frame.data}\n${value}`;
  }
  return frame;
}

export interface ConsumeSSEOptions {
  headers?: Record<string, string>;
  signal: AbortSignal;
  onEvent: (eventType: EventType | string, data: AgentEventData) => void;
  /** Called for a malformed/unparseable event -- non-fatal, stream continues. */
  onParseError?: (raw: RawSSEFrame) => void;
}

/**
 * Connect to a Server-Sent Events endpoint via `fetch` + a streamed
 * `ReadableStream`, rather than the native `EventSource` API --
 * `EventSource` cannot set custom request headers, and the backend's
 * stream endpoint (GET /api/v1/analysis/{id}/stream) requires
 * `Authorization: Bearer <token>` like every other v1 route (see
 * api/deps.py::get_current_user). This resolves once the stream ends
 * (server closed it, a terminal event was seen, or `signal` aborted).
 */
export async function consumeSSE(url: string, options: ConsumeSSEOptions): Promise<void> {
  const response = await fetch(url, {
    method: "GET",
    headers: { Accept: "text/event-stream", ...options.headers },
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`Failed to open event stream (status ${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? ""; // last element may be an incomplete frame

      for (const frameText of frames) {
        if (!frameText.trim()) continue;
        const frame = parseSSEFrame(frameText);
        if (!frame.event || !frame.data) continue; // heartbeat/comment-only frame

        try {
          const data = JSON.parse(frame.data) as AgentEventData;
          options.onEvent(frame.event, data);
        } catch {
          options.onParseError?.(frame);
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
