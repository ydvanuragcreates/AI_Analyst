import { Info } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { CorrelationResult } from "@/types/deep-analysis";

const STRENGTH_LABEL: Record<CorrelationResult["strength"], string> = {
  negligible: "Negligible",
  weak: "Weak",
  moderate: "Moderate",
  strong: "Strong",
  very_strong: "Very strong",
};

export function CorrelationCard({ correlation }: { correlation: CorrelationResult }) {
  return (
    <div className="rounded-xl border bg-card p-5">
      <h3 className="text-sm font-semibold">
        {correlation.column_a} <span className="text-muted-foreground">↔</span> {correlation.column_b}
      </h3>

      <div className="mt-3 flex items-baseline gap-3">
        <span className="text-3xl font-semibold tabular-nums">{correlation.coefficient.toFixed(2)}</span>
        <Badge
          variant="secondary"
          className={cn(
            correlation.strength === "strong" || correlation.strength === "very_strong"
              ? "bg-primary/10 text-primary"
              : "bg-muted text-muted-foreground"
          )}
        >
          {STRENGTH_LABEL[correlation.strength]} {correlation.direction !== "none" && correlation.direction}
        </Badge>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">Based on {correlation.sample_size} paired observations</p>

      <div className="mt-4 flex items-start gap-2 rounded-lg bg-muted/50 p-3 text-xs text-muted-foreground">
        <Info className="mt-0.5 size-3.5 shrink-0" />
        <span>Correlation does not imply causation. This measures a statistical relationship only.</span>
      </div>
    </div>
  );
}
