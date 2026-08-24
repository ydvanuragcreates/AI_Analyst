"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { AXIS_COLOR, CHART_COLORS, GRID_COLOR, TOOLTIP_STYLE } from "@/components/charts/chart-theme";
import type { VisualizationSpec } from "@/types/deep-analysis";

export function LineChartView({ spec }: { spec: VisualizationSpec }) {
  const xField = spec.x_axis?.field ?? "x";
  const yField = spec.y_axis?.field ?? "y";

  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={spec.data} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} vertical={false} />
        <XAxis dataKey={xField} stroke={AXIS_COLOR} fontSize={12} tickLine={false} axisLine={false} />
        <YAxis stroke={AXIS_COLOR} fontSize={12} tickLine={false} axisLine={false} width={56} />
        <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={{ color: "var(--popover-foreground)" }} />
        <Line type="monotone" dataKey={yField} stroke={CHART_COLORS[0]} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}
