import { BarChartView } from "@/components/charts/bar-chart-view";
import { HistogramView } from "@/components/charts/histogram-view";
import { LineChartView } from "@/components/charts/line-chart-view";
import { ScatterChartView } from "@/components/charts/scatter-chart-view";
import { TableView } from "@/components/charts/table-view";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { VisualizationSpec } from "@/types/deep-analysis";

/**
 * Consumes the backend's visualization data contract
 * (schemas/deep_analysis.py::VisualizationSpec) and picks a chart
 * component purely from `spec.type` -- the backend decides WHAT to
 * chart (agent/deep_analysis/nodes.py::chart_recommendation_node), this
 * only decides HOW to render that decision. No analytical logic lives
 * here.
 */
export function VisualizationRenderer({ spec }: { spec: VisualizationSpec }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{spec.title}</CardTitle>
      </CardHeader>
      <CardContent>
        <ChartByType spec={spec} />
      </CardContent>
    </Card>
  );
}

function ChartByType({ spec }: { spec: VisualizationSpec }) {
  switch (spec.type) {
    case "line":
      return <LineChartView spec={spec} />;
    case "bar":
      return <BarChartView spec={spec} />;
    case "scatter":
      return <ScatterChartView spec={spec} />;
    case "histogram":
      return <HistogramView spec={spec} />;
    case "table":
    case "box":
    case "heatmap":
    default:
      return <TableView spec={spec} />;
  }
}
