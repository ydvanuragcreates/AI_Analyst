// Mirrors backend/app/schemas/deep_analysis.py exactly -- the Phase 10
// autonomous deep-analysis pipeline's typed contracts. Field names and
// literal unions match the Pydantic models 1:1 so the API client never
// needs a translation layer.

import type { DatasetProfile } from "./dataset";

export type DataQualitySeverity = "low" | "medium" | "high";

export type DataQualityIssueType =
  | "missing_values"
  | "duplicate_rows"
  | "outliers"
  | "negative_values"
  | "implausible_range"
  | "inconsistent_categories"
  | "narrow_time_range";

export interface DataQualityIssue {
  issue_type: DataQualityIssueType;
  column: string | null;
  description: string;
  severity: DataQualitySeverity;
  evidence: Record<string, unknown>;
}

export interface DataQualityReport {
  issues: DataQualityIssue[];
  duplicate_row_count: number;
  total_row_count: number;
}

export type HypothesisPriority = "low" | "medium" | "high";
export type HypothesisStatus = "supported" | "rejected" | "inconclusive";
export type DeepAnalysisStepType = "profile" | "trend" | "anomaly" | "correlation" | "segmentation" | "hypothesis_test";

export interface Hypothesis {
  statement: string;
  evidence_required: string[];
  analytical_method: DeepAnalysisStepType;
  column: string | null;
  secondary_column: string | null;
  priority: HypothesisPriority;
}

export interface HypothesisResult {
  hypothesis: Hypothesis;
  status: HypothesisStatus;
  evidence: Record<string, unknown>;
  reasoning: string;
}

export type TrendDirection = "increasing" | "decreasing" | "flat" | "volatile";

export interface TrendPeriod {
  period: string;
  value: number;
  absolute_change: number | null;
  percentage_change: number | null;
}

export interface TrendResult {
  metric_column: string;
  date_column: string;
  granularity: "day" | "week" | "month";
  direction: TrendDirection;
  periods: TrendPeriod[];
  overall_absolute_change: number | null;
  overall_percentage_change: number | null;
  accelerating: boolean;
  sudden_changes: string[];
}

export type AnomalySeverity = "low" | "medium" | "high";

export interface AnomalyResult {
  metric_column: string;
  value: number;
  expected_range: string;
  method: "iqr" | "zscore";
  severity: AnomalySeverity;
  context: Record<string, unknown>;
}

export type CorrelationStrength = "negligible" | "weak" | "moderate" | "strong" | "very_strong";

export interface CorrelationResult {
  column_a: string;
  column_b: string;
  coefficient: number;
  strength: CorrelationStrength;
  direction: "positive" | "negative" | "none";
  sample_size: number;
}

export interface SegmentValue {
  segment: string;
  metric_value: number;
  row_count: number;
  contribution_percentage: number;
  growth_percentage: number | null;
}

export interface SegmentResult {
  dimension_column: string;
  metric_column: string;
  aggregation: "sum" | "avg" | "count";
  segments: SegmentValue[];
}

export interface ConfidenceBreakdown {
  data_completeness: number;
  sample_size_adequacy: number;
  statistical_strength: number;
  consistency: number;
  overall: number;
  reasons: string[];
}

export interface Insight {
  title: string;
  finding: string;
  evidence: Record<string, unknown>;
  metric: string | null;
  comparison: string | null;
  significance: string | null;
  confidence: ConfidenceBreakdown;
  implication: string;
  source_step_type: DeepAnalysisStepType;
}

export interface DeepReviewResult {
  approved: boolean;
  contradictions: string[];
  causal_language_violations: string[];
  unsupported_claims: string[];
  issues: string[];
  corrections: string[];
  confidence: number;
}

export interface Recommendation {
  fact: string;
  interpretation: string;
  recommendation: string;
  grounded_insight_title: string | null;
}

export interface DatasetOverview {
  row_count: number;
  column_count: number;
  numeric_column_count: number;
  categorical_column_count: number;
  date_column_count: number;
}

export interface Report {
  executive_summary: string;
  dataset_overview: DatasetOverview;
  data_quality: DataQualityIssue[];
  key_findings: Insight[];
  trends: TrendResult[];
  anomalies: AnomalyResult[];
  segment_analysis: SegmentResult[];
  correlations: CorrelationResult[];
  recommendations: Recommendation[];
  limitations: string[];
}

export type VisualizationType = "line" | "bar" | "scatter" | "histogram" | "box" | "heatmap" | "table";

export interface AxisSpec {
  field: string;
  label: string | null;
}

export interface VisualizationSpec {
  type: VisualizationType;
  title: string;
  x_axis: AxisSpec | null;
  y_axis: AxisSpec | null;
  data: Record<string, unknown>[];
}

export type TraceStatus = "completed" | "skipped" | "failed";

export interface TraceStep {
  step: string;
  status: TraceStatus;
  duration_ms: number;
  result_summary: string;
}

export type BudgetStatus = "ok" | "exceeded";

export interface AnalysisBudget {
  max_steps: number;
  max_llm_calls: number;
  max_query_executions: number;
  max_seconds: number;
  steps_used: number;
  llm_calls_used: number;
  query_executions_used: number;
  elapsed_seconds: number;
  status: BudgetStatus;
  exceeded_reason: string | null;
}

export type DeepAnalysisStatus = "completed" | "budget_exceeded" | "insufficient_data" | "failed";

export interface DeepAnalysisResponse {
  analysis_id: string | null;
  status: DeepAnalysisStatus;
  summary: string;
  insights: Insight[];
  trends: TrendResult[];
  anomalies: AnomalyResult[];
  correlations: CorrelationResult[];
  segments: SegmentResult[];
  hypothesis_results: HypothesisResult[];
  recommendations: Recommendation[];
  visualizations: VisualizationSpec[];
  data_quality: DataQualityIssue[];
  limitations: string[];
  report: Report | null;
  profile: DatasetProfile | null;
  trace: TraceStep[];
  budget: AnalysisBudget | null;
  review: DeepReviewResult | null;
  review_retry_count: number;
}
