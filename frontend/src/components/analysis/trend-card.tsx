import { Minus, TrendingDown, TrendingUp, Zap } from "lucide-react";

import { LineChartView } from "@/components/charts/line-chart-view";
import { Badge } from "@/components/ui/badge";
import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { TrendResult } from "@/types/deep-analysis";

const DIRECTION_CONFIG = {
  increasing: { icon: TrendingUp, label: "Increasing", className: "text-emerald-600 dark:text-emerald-400" },
  decreasing: { icon: TrendingDown, label: "Decreasing", className: "text-destructive" },
  flat: { icon: Minus, label: "Flat", className: "text-muted-foreground" },
  volatile: { icon: Zap, label: "Volatile", className: "text-amber-600 dark:text-amber-400" },
} as const;

export function TrendCard({ trend }: { trend: TrendResult }) {
  const config = DIRECTION_CONFIG[trend.direction];
  const Icon = config.icon;

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-muted-foreground">{trend.metric_column}</p>
          <div className="mt-1 flex items-center gap-2">
            <Icon className={cn("size-5", config.className)} />
            <span className={cn("text-lg font-semibold", config.className)}>{config.label}</span>
          </div>
        </div>
        <div className="text-right">
          {trend.overall_percentage_change !== null && (
            <p className={cn("text-2xl font-semibold tabular-nums", trend.overall_percentage_change >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive")}>
              {formatPercent(trend.overall_percentage_change, { signed: true })}
            </p>
          )}
          <p className="text-xs text-muted-foreground">over the observed period</p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-1.5">
        {trend.accelerating && <Badge variant="secondary">Accelerating</Badge>}
        {trend.sudden_changes.map((change) => (
          <Badge key={change} variant="secondary" className="bg-amber-500/10 text-amber-600 dark:text-amber-400">
            Sudden change: {change}
          </Badge>
        ))}
      </div>

      <div className="mt-4">
        <LineChartView
          spec={{
            type: "line",
            title: trend.metric_column,
            x_axis: { field: "period", label: "Period" },
            y_axis: { field: "value", label: trend.metric_column },
            data: trend.periods as unknown as Record<string, unknown>[],
          }}
        />
      </div>
    </div>
  );
}
