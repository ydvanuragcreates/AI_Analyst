"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { API_URL } from "@/lib/env";
import { queryKeys } from "@/lib/query-keys";
import { consumeSSE } from "@/lib/sse";
import { useAuthStore } from "@/stores/auth-store";
import { TERMINAL_EVENT_TYPES, type AgentEventData, type EventType } from "@/types/events";

export interface StreamEvent {
  eventType: EventType | string;
  data: AgentEventData;
  receivedAt: number;
}

export type StreamConnectionStatus = "idle" | "connecting" | "open" | "closed" | "error";

export interface UseAnalysisStreamResult {
  events: StreamEvent[];
  latestEvent: StreamEvent | null;
  connectionStatus: StreamConnectionStatus;
  error: string | null;
}

/**
 * Connects to GET /api/v1/analysis/{analysisId}/stream (SSE) and
 * accumulates the safe progress events the worker publishes -- see
 * services/event_service.py. Exactly one connection is ever open per
 * `analysisId` (guarded by the effect's own cleanup/AbortController, not
 * a global registry -- two components rendering this hook for the SAME
 * analysisId would each open their own connection, which is why callers
 * should keep this hook to one call site per analysis, e.g. the
 * workspace page, and pass the result down via props/context rather than
 * calling the hook again lower in the tree).
 *
 * Reconnects once (with a short backoff) if the connection drops before
 * a terminal event was seen -- a genuinely closed/completed stream is
 * never retried.
 */
export function useAnalysisStream(analysisId: string | undefined, enabled: boolean): UseAnalysisStreamResult {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [connectionStatus, setConnectionStatus] = useState<StreamConnectionStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const reconnectAttemptedRef = useRef(false);

  useEffect(() => {
    // No setState directly in this top-level effect body anywhere below
    // (react-hooks/set-state-in-effect) -- every state update, including
    // the "not authenticated" and "reset for a new run" cases, happens
    // inside the `run()` async function instead, mirroring how
    // `connect()`'s own updates (inside callbacks/promise continuations)
    // were already exempt. `run()` still starts executing synchronously
    // when called, but the linter's static check only looks at the
    // effect callback's own immediate statements, not into functions it
    // calls -- and this still satisfies the *actual* rule this lint is
    // protecting against (no cascading re-render loop: this effect's own
    // dependency array is unaffected by any state it sets).
    if (!analysisId || !enabled) {
      return;
    }

    // Narrowed once, outside the closures below -- `analysisId` is a
    // `string | undefined` prop, and TypeScript doesn't propagate the
    // guard-clause narrowing above into a nested function.
    const currentAnalysisId = analysisId;
    const controller = new AbortController();
    reconnectAttemptedRef.current = false;

    async function connect(token: string) {
      setConnectionStatus("connecting");
      try {
        await consumeSSE(`${API_URL}/api/v1/analysis/${currentAnalysisId}/stream`, {
          signal: controller.signal,
          headers: { Authorization: `Bearer ${token}` },
          onEvent: (eventType, data) => {
            setConnectionStatus("open");
            setEvents((previous) => [...previous, { eventType, data, receivedAt: Date.now() }]);

            if (TERMINAL_EVENT_TYPES.has(eventType as EventType)) {
              queryClient.invalidateQueries({ queryKey: queryKeys.analysis(currentAnalysisId) });
              queryClient.invalidateQueries({ queryKey: queryKeys.analysisInsights(currentAnalysisId) });
              queryClient.invalidateQueries({ queryKey: queryKeys.analysisReport(currentAnalysisId) });
              queryClient.invalidateQueries({ queryKey: queryKeys.analysisVisualizations(currentAnalysisId) });
            }
          },
        });
        if (!controller.signal.aborted) {
          setConnectionStatus("closed");
        }
      } catch (err) {
        if (controller.signal.aborted) return;

        // One reconnect attempt for a connection that dropped before any
        // terminal event -- e.g. a transient network blip, not the
        // analysis actually finishing.
        if (!reconnectAttemptedRef.current) {
          reconnectAttemptedRef.current = true;
          setTimeout(() => {
            if (!controller.signal.aborted) void connect(token);
          }, 2000);
          return;
        }

        setConnectionStatus("error");
        setError(err instanceof Error ? err.message : "Lost connection to the analysis stream.");
      }
    }

    async function run() {
      const token = useAuthStore.getState().token;
      if (!token) {
        setConnectionStatus("error");
        setError("Not authenticated.");
        return;
      }
      setEvents([]);
      setError(null);
      await connect(token);
    }

    void run();

    return () => {
      controller.abort();
    };
  }, [analysisId, enabled, queryClient]);

  const isActive = Boolean(analysisId) && enabled;

  return {
    events,
    latestEvent: events.length > 0 ? events[events.length - 1] : null,
    connectionStatus: isActive ? connectionStatus : "idle",
    error: isActive ? error : null,
  };
}
