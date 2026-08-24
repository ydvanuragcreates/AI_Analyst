import { BarChartView } from "@/components/charts/bar-chart-view";
import { formatNumber, formatPercent } from "@/lib/format";
import type { SegmentResult } from "@/types/deep-analysis";

export function SegmentCard({ segment }: { segment: SegmentResult }) {
  const top = segment.segments[0];

  return (
    <div className="rounded-xl border bg-card p-5">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">
        {segment.metric_column} by {segment.dimension_column}
      </p>
      {top && (
        <p className="mt-1 text-sm">
          <span className="font-semibold">{top.segment}</span> contributes{" "}
          <span className="font-semibold">{formatPercent(top.contribution_percentage)}</span> of total {segment.metric_column}
        </p>
      )}

      <div className="mt-4">
        <BarChartView
          spec={{
            type: "bar",
            title: segment.metric_column,
            x_axis: { field: "segment", label: segment.dimension_column },
            y_axis: { field: "metric_value", label: segment.metric_column },
            data: segment.segments as unknown as Record<string, unknown>[],
          }}
        />
      </div>

      <div className="mt-3 space-y-1.5">
        {segment.segments.map((value) => (
          <div key={value.segment} className="flex items-center justify-between text-xs">
            <span className="font-medium">{value.segment}</span>
            <span className="text-muted-foreground">
              {formatNumber(value.metric_value)} · {formatPercent(value.contribution_percentage)}
              {value.growth_percentage !== null && <> · {formatPercent(value.growth_percentage, { signed: true })} growth</>}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
