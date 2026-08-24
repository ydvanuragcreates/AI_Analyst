import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { InsightCard } from "@/components/analysis/insight-card";
import type { Insight } from "@/types/deep-analysis";

const insight: Insight = {
  title: "Revenue declined 8.4%",
  finding: "July revenue was lower than June revenue.",
  evidence: { june_revenue: 5100000, july_revenue: 4700000, change_percentage: -8.4 },
  metric: "Revenue",
  comparison: "June vs July",
  significance: null,
  confidence: { data_completeness: 1, sample_size_adequacy: 1, statistical_strength: 0.9, consistency: 1, overall: 0.92, reasons: [] },
  implication: "Investigate the July decline before making broader decisions.",
  source_step_type: "trend",
};

describe("InsightCard", () => {
  it("shows the title, finding, implication, and confidence percentage", () => {
    render(<InsightCard insight={insight} />);
    expect(screen.getByText("Revenue declined 8.4%")).toBeInTheDocument();
    expect(screen.getByText(/July revenue was lower/)).toBeInTheDocument();
    expect(screen.getByText(/Investigate the July decline/)).toBeInTheDocument();
    expect(screen.getByText("Confidence 92%")).toBeInTheDocument();
  });

  it("hides the raw evidence until 'View evidence' is expanded (evidence-first, not evidence-dumped)", async () => {
    render(<InsightCard insight={insight} />);
    expect(screen.queryByText("5,100,000")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /view evidence/i }));

    expect(screen.getByText("5,100,000")).toBeInTheDocument();
    expect(screen.getByText("4,700,000")).toBeInTheDocument();
  });

  it("labels the source type with text, not color alone", () => {
    render(<InsightCard insight={insight} />);
    expect(screen.getByText("Trend")).toBeInTheDocument();
  });
});
