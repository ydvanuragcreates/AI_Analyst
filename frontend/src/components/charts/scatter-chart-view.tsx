"use client";

import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";

import { AXIS_COLOR, CHART_COLORS, GRID_COLOR, TOOLTIP_STYLE } from "@/components/charts/chart-theme";
import type { VisualizationSpec } from "@/types/deep-analysis";

export function ScatterChartView({ spec }: { spec: VisualizationSpec }) {
  const xField = spec.x_axis?.field ?? "x";
  const yField = spec.y_axis?.field ?? "y";

  return (
    <ResponsiveContainer width="100%" height={320}>
      <ScatterChart margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
        <XAxis
          dataKey={xField}
          type="number"
          name={spec.x_axis?.label ?? xField}
          stroke={AXIS_COLOR}
          fontSize={12}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          dataKey={yField}
          type="number"
          name={spec.y_axis?.label ?? yField}
          stroke={AXIS_COLOR}
          fontSize={12}
          tickLine={false}
          axisLine={false}
          width={56}
        />
        <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ strokeDasharray: "3 3", stroke: GRID_COLOR }} />
        <Scatter data={spec.data} fill={CHART_COLORS[2]} fillOpacity={0.7} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}
