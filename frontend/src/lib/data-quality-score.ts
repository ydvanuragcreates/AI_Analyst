import type { DataQualityIssue } from "@/types/deep-analysis";

/**
 * A single at-a-glance quality score, derived client-side from the
 * backend's already-computed issue list (services/data_quality_service.py
 * never computes a single score itself -- only typed issues with a
 * severity). This is pure presentation: a weighted deduction by
 * severity, not a new analytical judgment -- every number it's built
 * from was already computed by the backend.
 */
export function computeQualityScore(issues: DataQualityIssue[]): number {
  const weight: Record<DataQualityIssue["severity"], number> = { low: 3, high: 15, medium: 8 };
  const deduction = issues.reduce((total, issue) => total + weight[issue.severity], 0);
  return Math.max(0, 100 - deduction);
}

export function qualityScoreLabel(score: number): string {
  if (score >= 90) return "Excellent";
  if (score >= 75) return "Good";
  if (score >= 50) return "Needs attention";
  return "Poor";
}
