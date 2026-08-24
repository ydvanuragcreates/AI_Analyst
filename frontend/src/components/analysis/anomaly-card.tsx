import { AlertTriangle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { AnomalyResult } from "@/types/deep-analysis";

const SEVERITY_STYLES = {
  low: "bg-muted text-muted-foreground",
  medium: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  high: "bg-destructive/10 text-destructive",
} as const;

export function AnomalyCard({ anomaly }: { anomaly: AnomalyResult }) {
  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="flex items-start gap-3">
        <div className={cn("flex size-9 shrink-0 items-center justify-center rounded-full", SEVERITY_STYLES[anomaly.severity])}>
          <AlertTriangle className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold">Unusual {anomaly.metric_column}</h3>
            <Badge variant="secondary" className={cn("text-xs capitalize", SEVERITY_STYLES[anomaly.severity])}>
              {anomaly.severity} severity
            </Badge>
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-xs text-muted-foreground">Value</dt>
              <dd className="font-semibold tabular-nums">{anomaly.value.toLocaleString("en-US")}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Expected range</dt>
              <dd className="font-medium tabular-nums">{anomaly.expected_range}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Detection method</dt>
              <dd className="font-medium uppercase">{anomaly.method}</dd>
            </div>
          </dl>
        </div>
      </div>
    </div>
  );
}
