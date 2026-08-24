import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CorrelationCard } from "@/components/analysis/correlation-card";
import type { CorrelationResult } from "@/types/deep-analysis";

describe("CorrelationCard", () => {
  it("always displays the 'correlation does not imply causation' disclaimer", () => {
    const correlation: CorrelationResult = {
      column_a: "discount",
      column_b: "sales_volume",
      coefficient: 0.78,
      strength: "strong",
      direction: "positive",
      sample_size: 120,
    };
    render(<CorrelationCard correlation={correlation} />);

    expect(screen.getByText(/does not imply causation/i)).toBeInTheDocument();
    expect(screen.getByText("0.78")).toBeInTheDocument();
    expect(screen.getByText(/discount/)).toBeInTheDocument();
    expect(screen.getByText(/sales_volume/)).toBeInTheDocument();
  });

  it("shows the disclaimer even for a negligible correlation", () => {
    const correlation: CorrelationResult = {
      column_a: "a",
      column_b: "b",
      coefficient: 0.02,
      strength: "negligible",
      direction: "none",
      sample_size: 20,
    };
    render(<CorrelationCard correlation={correlation} />);
    expect(screen.getByText(/does not imply causation/i)).toBeInTheDocument();
  });
});
