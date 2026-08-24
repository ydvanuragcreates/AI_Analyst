// Mirrors backend/app/services/event_service.py's AgentEvent /
// EventType exactly -- the safe, structured SSE progress events the
// worker publishes. Never chain-of-thought or raw prompts, only "which
// stage just happened."

export type EventType =
  | "analysis_started"
  | "agent_started"
  | "planner_completed"
  | "sql_started"
  | "sql_completed"
  | "review_started"
  | "approval_required"
  | "analysis_completed"
  | "analysis_failed"
  | "analysis_cancelled"
  // Phase 10 deep-analysis worker path (workers/analysis_worker.py::_publish_deep_node_event)
  | "profiling_completed"
  | "data_quality_completed"
  | "hypotheses_generated"
  | "analysis_step_completed"
  | "insights_generated"
  | "report_completed";

export const TERMINAL_EVENT_TYPES: ReadonlySet<EventType> = new Set([
  "analysis_completed",
  "analysis_failed",
  "analysis_cancelled",
  "approval_required",
]);

/** The parsed shape of one SSE frame's `data:` payload -- see
 * event_service.py::format_sse: `{analysis_id, event_id, ...payload}`. */
export interface AgentEventData {
  analysis_id: string;
  event_id: string;
  [key: string]: unknown;
}

export interface ParsedSSEEvent {
  id: string | null;
  event: EventType | string;
  data: AgentEventData;
}
