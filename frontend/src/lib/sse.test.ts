import { describe, expect, it } from "vitest";

import { parseSSEFrame } from "@/lib/sse";

describe("parseSSEFrame", () => {
  it("parses id/event/data fields from a real backend frame", () => {
    const frame = parseSSEFrame('id: abc123\nevent: analysis_completed\ndata: {"analysis_id":"a1","event_id":"abc123","status":"completed"}');
    expect(frame.id).toBe("abc123");
    expect(frame.event).toBe("analysis_completed");
    expect(JSON.parse(frame.data as string)).toEqual({ analysis_id: "a1", event_id: "abc123", status: "completed" });
  });

  it("treats a bare comment line (heartbeat) as having no event/data", () => {
    const frame = parseSSEFrame(": keep-alive");
    expect(frame.event).toBeNull();
    expect(frame.data).toBeNull();
  });

  it("ignores blank lines within a frame", () => {
    const frame = parseSSEFrame("id: x\n\nevent: sql_started\ndata: {}");
    expect(frame.id).toBe("x");
    expect(frame.event).toBe("sql_started");
  });

  it("handles a data value that itself contains a colon", () => {
    const frame = parseSSEFrame('event: report_completed\ndata: {"url":"http://x"}');
    expect(frame.data).toBe('{"url":"http://x"}');
  });
});
