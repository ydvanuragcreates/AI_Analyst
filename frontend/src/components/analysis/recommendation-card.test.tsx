import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RecommendationCard } from "@/components/analysis/recommendation-card";
import type { Recommendation } from "@/types/deep-analysis";

describe("RecommendationCard", () => {
  it("keeps fact, interpretation, and recommendation as distinctly labeled sections", () => {
    const recommendation: Recommendation = {
      fact: "North revenue declined 12%.",
      interpretation: "North's performance is weakening relative to other regions.",
      recommendation: "Investigate North region performance before applying a company-wide pricing strategy.",
      grounded_insight_title: "North region decline",
    };
    render(<RecommendationCard recommendation={recommendation} />);

    expect(screen.getByText("Fact")).toBeInTheDocument();
    expect(screen.getByText("Interpretation")).toBeInTheDocument();
    // "Recommendation" legitimately appears twice: the card's own eyebrow
    // label and the highlighted section's <dt> -- both are real, distinct
    // labels, not a duplicate render bug.
    expect(screen.getAllByText("Recommendation")).toHaveLength(2);
    expect(screen.getByText("North revenue declined 12%.")).toBeInTheDocument();
    expect(screen.getByText(/Investigate North region performance/)).toBeInTheDocument();
  });
});
