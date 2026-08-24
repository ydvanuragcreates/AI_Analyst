"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { AXIS_COLOR, CHART_COLORS, GRID_COLOR, TOOLTIP_STYLE } from "@/components/charts/chart-theme";
import type { VisualizationSpec } from "@/types/deep-analysis";

/** Renders a histogram exactly like a bar chart -- the shape (bucket
 * label on x, count on y) is the same, just semantically a distribution
 * rather than a category comparison. */
export function HistogramView({ spec }: { spec: VisualizationSpec }) {
  const xField = spec.x_axis?.field ?? "bucket";
  const yField = spec.y_axis?.field ?? "count";

  return (
    <ResponsiveContainer width="100%" height={320}>
      <BarChart data={spec.data} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} vertical={false} />
        <XAxis dataKey={xField} stroke={AXIS_COLOR} fontSize={12} tickLine={false} axisLine={false} />
        <YAxis stroke={AXIS_COLOR} fontSize={12} tickLine={false} axisLine={false} width={48} />
        <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "var(--muted)" }} />
        <Bar dataKey={yField} fill={CHART_COLORS[1]} radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
