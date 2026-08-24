"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Circle, CircleDot, CheckCircle2, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import type { AnalysisMode } from "@/types/analysis";
import type { EventType } from "@/types/events";
import type { StreamEvent } from "@/hooks/use-analysis-stream";

interface StepDefinition {
  label: string;
  eventTypes: EventType[];
}

// Mirrors the real node names each graph publishes (see
// workers/analysis_worker.py::_publish_node_event / _publish_deep_node_event)
// -- every label here corresponds to an actual backend stage, not a
// fabricated one, and only advances when that stage's real event arrives.
const QA_STEPS: StepDefinition[] = [
  { label: "Understanding your question", eventTypes: ["agent_started"] },
  { label: "Planning the analysis", eventTypes: ["planner_completed"] },
  { label: "Running SQL analysis", eventTypes: ["sql_started", "sql_completed"] },
  { label: "Reviewing findings", eventTypes: ["review_started"] },
];

const DEEP_STEPS: StepDefinition[] = [
  { label: "Profiling dataset", eventTypes: ["profiling_completed"] },
  { label: "Checking data quality", eventTypes: ["data_quality_completed"] },
  { label: "Creating analysis plan", eventTypes: ["planner_completed"] },
  { label: "Generating hypotheses", eventTypes: ["hypotheses_generated"] },
  { label: "Detecting trends & anomalies", eventTypes: ["analysis_step_completed"] },
  { label: "Generating insights", eventTypes: ["insights_generated"] },
  { label: "Reviewing findings", eventTypes: ["review_started"] },
  { label: "Building report", eventTypes: ["report_completed"] },
];

type StepStatus = "done" | "active" | "pending" | "failed";

function computeStepStatuses(steps: StepDefinition[], events: StreamEvent[], isTerminalFailure: boolean, isTerminalSuccess: boolean): StepStatus[] {
  const seen = new Set(events.map((event) => event.eventType));
  let lastCompletedIndex = -1;
  steps.forEach((step, index) => {
    if (step.eventTypes.some((type) => seen.has(type))) lastCompletedIndex = index;
  });

  if (isTerminalSuccess) return steps.map(() => "done");

  return steps.map((_, index) => {
    if (index <= lastCompletedIndex) return "done";
    if (index === lastCompletedIndex + 1) return isTerminalFailure ? "failed" : "active";
    return "pending";
  });
}

export function AnalysisProgress({
  mode,
  events,
  isTerminalFailure,
  isTerminalSuccess,
}: {
  mode: AnalysisMode;
  events: StreamEvent[];
  isTerminalFailure: boolean;
  isTerminalSuccess: boolean;
}) {
  const steps = mode === "deep" ? DEEP_STEPS : QA_STEPS;
  const statuses = computeStepStatuses(steps, events, isTerminalFailure, isTerminalSuccess);

  return (
    <div className="rounded-xl border bg-card p-5">
      <p className="mb-4 text-sm font-medium">Analyzing your dataset...</p>
      <ul className="space-y-3">
        <AnimatePresence initial={false}>
          {steps.map((step, index) => (
            <motion.li
              key={step.label}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2 }}
              className="flex items-center gap-3 text-sm"
            >
              <StepIcon status={statuses[index]} />
              <span className={cn(statuses[index] === "pending" ? "text-muted-foreground" : "text-foreground", statuses[index] === "done" && "text-muted-foreground")}>
                {step.label}
                {statuses[index] === "active" && "..."}
              </span>
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
    </div>
  );
}

function StepIcon({ status }: { status: StepStatus }) {
  if (status === "done") return <CheckCircle2 className="size-4 shrink-0 text-emerald-500" aria-label="Completed" />;
  if (status === "active") return <CircleDot className="size-4 shrink-0 animate-pulse text-primary" aria-label="In progress" />;
  if (status === "failed") return <XCircle className="size-4 shrink-0 text-destructive" aria-label="Failed" />;
  return <Circle className="size-4 shrink-0 text-muted-foreground/40" aria-label="Pending" />;
}
