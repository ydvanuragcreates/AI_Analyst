import { AlertTriangle, CheckCircle2, Copy, HelpCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { computeQualityScore, qualityScoreLabel } from "@/lib/data-quality-score";
import { cn } from "@/lib/utils";
import type { DataQualityIssue } from "@/types/deep-analysis";

const SEVERITY_STYLES: Record<DataQualityIssue["severity"], string> = {
  low: "bg-muted text-muted-foreground",
  medium: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  high: "bg-destructive/10 text-destructive",
};

const ISSUE_LABELS: Record<DataQualityIssue["issue_type"], string> = {
  missing_values: "Missing values",
  duplicate_rows: "Duplicate rows",
  outliers: "Outliers",
  negative_values: "Negative values",
  implausible_range: "Implausible range",
  inconsistent_categories: "Inconsistent categories",
  narrow_time_range: "Narrow time range",
};

export interface DataQualityPanelProps {
  issues: DataQualityIssue[];
  /** Only available from the standalone dataset quality endpoint
   * (DataQualityReport) -- an analysis result's flattened issue list
   * (DeepAnalysisResponse.data_quality / Report.data_quality) doesn't
   * carry these separately, so they're derived from the duplicate_rows
   * issue's own evidence when omitted. */
  totalRowCount?: number;
  duplicateRowCount?: number;
}

export function DataQualityPanel({ issues, totalRowCount, duplicateRowCount }: DataQualityPanelProps) {
  const score = computeQualityScore(issues);
  const duplicateIssue = issues.find((issue) => issue.issue_type === "duplicate_rows");
  const resolvedDuplicateCount = duplicateRowCount ?? (duplicateIssue?.evidence.duplicate_count as number | undefined) ?? 0;
  const resolvedTotalCount = totalRowCount ?? (duplicateIssue?.evidence.total_rows as number | undefined);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 rounded-xl border p-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Overall quality</p>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="text-3xl font-semibold tabular-nums">{score}</span>
            <span className="text-sm text-muted-foreground">/ 100</span>
          </div>
          <p className="mt-0.5 text-sm text-muted-foreground">{qualityScoreLabel(score)}</p>
        </div>
        <div className="w-full sm:w-56">
          <Progress value={score} />
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <QuickFact
          icon={Copy}
          label="Duplicate rows"
          value={resolvedTotalCount !== undefined ? `${resolvedDuplicateCount} of ${resolvedTotalCount}` : String(resolvedDuplicateCount)}
          ok={resolvedDuplicateCount === 0}
        />
        <QuickFact
          icon={issues.length === 0 ? CheckCircle2 : AlertTriangle}
          label="Issues detected"
          value={String(issues.length)}
          ok={issues.length === 0}
        />
      </div>

      {issues.length === 0 ? (
        <div className="flex items-center gap-2 rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
          <CheckCircle2 className="size-4 text-emerald-500" />
          No data quality issues were detected.
        </div>
      ) : (
        <div className="space-y-2">
          {issues.map((issue, index) => (
            <div key={index} className="flex items-start gap-3 rounded-lg border p-3">
              <div className={cn("mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full", SEVERITY_STYLES[issue.severity])}>
                {issue.severity === "high" ? <AlertTriangle className="size-3.5" /> : <HelpCircle className="size-3.5" />}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{ISSUE_LABELS[issue.issue_type]}</span>
                  {issue.column && (
                    <Badge variant="outline" className="font-mono text-xs">
                      {issue.column}
                    </Badge>
                  )}
                  <Badge variant="secondary" className={cn("text-xs capitalize", SEVERITY_STYLES[issue.severity])}>
                    {issue.severity}
                  </Badge>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">{issue.description}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function QuickFact({ icon: Icon, label, value, ok }: { icon: typeof Copy; label: string; value: string; ok: boolean }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border p-3">
      <div className={cn("flex size-8 shrink-0 items-center justify-center rounded-full", ok ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" : "bg-amber-500/10 text-amber-600 dark:text-amber-400")}>
        <Icon className="size-4" />
      </div>
      <div>
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-sm font-medium tabular-nums">{value}</p>
      </div>
    </div>
  );
}
