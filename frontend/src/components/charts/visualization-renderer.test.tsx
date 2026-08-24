import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { VisualizationRenderer } from "@/components/charts/visualization-renderer";
import type { VisualizationSpec } from "@/types/deep-analysis";

function spec(overrides: Partial<VisualizationSpec>): VisualizationSpec {
  return {
    type: "bar",
    title: "Test chart",
    x_axis: { field: "x", label: null },
    y_axis: { field: "y", label: null },
    data: [{ x: "A", y: 1 }],
    ...overrides,
  };
}

describe("VisualizationRenderer", () => {
  it("always renders the chart title", () => {
    render(<VisualizationRenderer spec={spec({ title: "Revenue by region" })} />);
    expect(screen.getByText("Revenue by region")).toBeInTheDocument();
  });

  it("falls back to a table for an unknown/unsupported type rather than rendering nothing", () => {
    render(<VisualizationRenderer spec={spec({ type: "heatmap", data: [{ x: "A", y: 1 }] })} />);
    // TableView renders a real <table> -- confirms the fallback path works.
    expect(document.querySelector("table")).not.toBeNull();
  });

  it("renders a table for the 'table' type with empty data without crashing", () => {
    render(<VisualizationRenderer spec={spec({ type: "table", data: [] })} />);
    expect(screen.getByText(/no data to display/i)).toBeInTheDocument();
  });
});
