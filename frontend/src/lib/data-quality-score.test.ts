import { describe, expect, it } from "vitest";

import { computeQualityScore, qualityScoreLabel } from "@/lib/data-quality-score";
import type { DataQualityIssue } from "@/types/deep-analysis";

function issue(severity: DataQualityIssue["severity"]): DataQualityIssue {
  return { issue_type: "missing_values", column: "x", description: "d", severity, evidence: {} };
}

describe("computeQualityScore", () => {
  it("returns 100 for no issues", () => {
    expect(computeQualityScore([])).toBe(100);
  });

  it("deducts more for higher severity issues", () => {
    const lowScore = computeQualityScore([issue("low")]);
    const mediumScore = computeQualityScore([issue("medium")]);
    const highScore = computeQualityScore([issue("high")]);
    expect(lowScore).toBeGreaterThan(mediumScore);
    expect(mediumScore).toBeGreaterThan(highScore);
  });

  it("never goes below 0", () => {
    const manyIssues = Array.from({ length: 20 }, () => issue("high"));
    expect(computeQualityScore(manyIssues)).toBe(0);
  });
});

describe("qualityScoreLabel", () => {
  it("labels scores in descending bands", () => {
    expect(qualityScoreLabel(95)).toBe("Excellent");
    expect(qualityScoreLabel(80)).toBe("Good");
    expect(qualityScoreLabel(60)).toBe("Needs attention");
    expect(qualityScoreLabel(20)).toBe("Poor");
  });
});
