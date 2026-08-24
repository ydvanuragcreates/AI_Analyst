"use client";

import { ChevronDown, GitCompare, Layers, LineChart as LineChartIcon, ShieldAlert, Sparkles } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import type { DeepAnalysisStepType, Insight } from "@/types/deep-analysis";

const SOURCE_CONFIG: Record<DeepAnalysisStepType, { icon: typeof Sparkles; label: string }> = {
  trend: { icon: LineChartIcon, label: "Trend" },
  anomaly: { icon: ShieldAlert, label: "Anomaly" },
  correlation: { icon: GitCompare, label: "Correlation" },
  segmentation: { icon: Layers, label: "Segment" },
  hypothesis_test: { icon: Sparkles, label: "Finding" },
  profile: { icon: Sparkles, label: "Finding" },
};

export function InsightCard({ insight }: { insight: Insight }) {
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const config = SOURCE_CONFIG[insight.source_step_type] ?? SOURCE_CONFIG.hypothesis_test;
  const Icon = config.icon;
  const confidencePercent = Math.round(insight.confidence.overall * 100);

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <Icon className="size-4" />
          </div>
          <Badge variant="outline" className="text-xs font-normal">
            {config.label}
          </Badge>
        </div>
      </div>

      <h3 className="mt-3 text-base font-semibold leading-snug">{insight.title}</h3>
      <p className="mt-1.5 text-sm text-muted-foreground">{insight.finding}</p>

      {(insight.metric || insight.comparison) && (
        <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-sm">
          {insight.metric && (
            <div>
              <span className="text-muted-foreground">Metric: </span>
              <span className="font-medium">{insight.metric}</span>
            </div>
          )}
          {insight.comparison && (
            <div>
              <span className="text-muted-foreground">Comparison: </span>
              <span className="font-medium">{insight.comparison}</span>
            </div>
          )}
        </div>
      )}

      <p className="mt-3 rounded-lg bg-muted/50 p-3 text-sm text-muted-foreground">
        <span className="font-medium text-foreground">Implication: </span>
        {insight.implication}
      </p>

      <button
        type="button"
        onClick={() => setEvidenceOpen((open) => !open)}
        className="mt-4 flex items-center gap-1 text-sm font-medium text-foreground underline-offset-4 hover:underline"
        aria-expanded={evidenceOpen}
      >
        View evidence
        <ChevronDown className={cn("size-4 transition-transform", evidenceOpen && "rotate-180")} />
      </button>

      {evidenceOpen && (
        <div className="mt-3 space-y-3 rounded-lg border bg-muted/30 p-3 text-xs">
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {Object.entries(insight.evidence)
              .filter(([, value]) => typeof value !== "object" || value === null)
              .map(([key, value]) => (
                <div key={key} className="min-w-0">
                  <dt className="truncate text-muted-foreground">{key.replace(/_/g, " ")}</dt>
                  <dd className="truncate font-medium text-foreground">{formatEvidenceValue(value)}</dd>
                </div>
              ))}
          </dl>
        </div>
      )}

      <div className="mt-4 flex items-center gap-3 border-t pt-4">
        <span className="shrink-0 text-xs font-medium text-muted-foreground">Confidence {confidencePercent}%</span>
        <Progress value={confidencePercent} className="h-1.5" />
      </div>
    </div>
  );
}

function formatEvidenceValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString("en-US") : value.toFixed(2);
  return String(value);
}
