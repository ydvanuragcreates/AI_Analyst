"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { AXIS_COLOR, CHART_COLORS, GRID_COLOR, TOOLTIP_STYLE } from "@/components/charts/chart-theme";
import type { VisualizationSpec } from "@/types/deep-analysis";

export function BarChartView({ spec }: { spec: VisualizationSpec }) {
  const xField = spec.x_axis?.field ?? "x";
  const yField = spec.y_axis?.field ?? "y";

  return (
    <ResponsiveContainer width="100%" height={320}>
      <BarChart data={spec.data} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} vertical={false} />
        <XAxis dataKey={xField} stroke={AXIS_COLOR} fontSize={12} tickLine={false} axisLine={false} />
        <YAxis stroke={AXIS_COLOR} fontSize={12} tickLine={false} axisLine={false} width={56} />
        <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "var(--muted)" }} />
        <Bar dataKey={yField} fill={CHART_COLORS[0]} radius={[4, 4, 0, 0]} maxBarSize={64} />
      </BarChart>
    </ResponsiveContainer>
  );
}
