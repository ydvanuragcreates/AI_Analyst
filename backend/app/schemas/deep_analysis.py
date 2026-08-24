"""
Phase 10: schemas for autonomous dataset investigation.

Everything here is either (a) the typed output of a *deterministic*
computation (profiling, data quality, trend/anomaly/correlation/
segmentation math) or (b) a narrow, structured-output contract an LLM call
is forced through. Nothing here is a free-form dict -- same rule
schemas/analysis.py already established for Phase 3-8, extended to the
deep-analysis pipeline (agent/deep_analysis/).

Kept in its own module rather than appended to schemas/analysis.py: Phase
10 adds ~20 new types for a genuinely distinct pipeline (profiling,
quality, hypotheses, insights, report, visualization contract, budget,
trace) -- folding all of that into the existing analysis.py would bury the
Phase 3-8 Q&A contracts the existing graphs still depend on.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

# ====================================================================== #
# 1. Dataset profiling (deterministic -- see services/profiling_service.py)
# ====================================================================== #


class ColumnProfile(BaseModel):
    """Deterministic profile of one column. Every numeric field here comes
    directly from a DuckDB aggregate query -- never estimated or invented
    by a model."""

    name: str
    dtype: str = Field(description="The DuckDB column type, e.g. 'BIGINT', 'VARCHAR'")
    kind: Literal["numeric", "categorical", "date", "boolean", "other"]
    missing_count: int = Field(ge=0)
    missing_percentage: float = Field(ge=0.0, le=100.0)
    distinct_count: int = Field(ge=0)
    is_likely_identifier: bool = Field(
        default=False, description="distinct_count == row_count (and row_count > 0), or the name looks like an id column"
    )
    is_high_cardinality: bool = Field(
        default=False, description="distinct_count is a large fraction of row_count, e.g. free-text or an id-like column"
    )

    # Numeric-only (None for non-numeric columns).
    min_value: float | None = None
    max_value: float | None = None
    mean: float | None = None
    median: float | None = None
    stddev: float | None = None
    q1: float | None = None
    q3: float | None = None

    # Date-only.
    min_date: str | None = None
    max_date: str | None = None

    # Categorical-only.
    top_values: list[dict[str, Any]] = Field(
        default_factory=list, description="Up to 5 most frequent values as [{'value':..., 'count':...}]"
    )


class DatasetProfile(BaseModel):
    """The autonomous profiling stage's output (section 1). Built once per
    analysis via profiling_service.profile_dataset -- never re-derived by
    a model, and never a vehicle for raw rows: only this compact summary
    is ever sent to an LLM."""

    dataset_id: str
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    columns: list[ColumnProfile]
    numeric_columns: list[str] = Field(default_factory=list)
    categorical_columns: list[str] = Field(default_factory=list)
    date_columns: list[str] = Field(default_factory=list)
    missing_columns: list[str] = Field(default_factory=list, description="Columns with any missing values")
    high_cardinality_columns: list[str] = Field(default_factory=list)
    identifier_columns: list[str] = Field(default_factory=list)


# ====================================================================== #
# 2. Data quality (deterministic -- see services/data_quality_service.py)
# ====================================================================== #

DataQualitySeverity = Literal["low", "medium", "high"]


class DataQualityIssue(BaseModel):
    issue_type: Literal[
        "missing_values", "duplicate_rows", "outliers", "negative_values",
        "implausible_range", "inconsistent_categories", "narrow_time_range",
    ]
    column: str | None = None
    description: str
    severity: DataQualitySeverity
    evidence: dict[str, Any] = Field(default_factory=dict, description="The real numbers backing this flag, e.g. {'min': -4, 'max': 137}")


class DataQualityReport(BaseModel):
    issues: list[DataQualityIssue] = Field(default_factory=list)
    duplicate_row_count: int = Field(default=0, ge=0)
    total_row_count: int = Field(default=0, ge=0)

    @property
    def is_clean(self) -> bool:
        return not self.issues


# ====================================================================== #
# 3/4. EDA planning (LLM, grounded against DatasetProfile)
# ====================================================================== #

DeepAnalysisStepType = Literal["profile", "trend", "anomaly", "correlation", "segmentation", "hypothesis_test"]


class DeepAnalysisStep(BaseModel):
    """One entry in the Planner's structured plan (section 4). Purely a
    *selection* of which deterministic analyses to run and on which
    columns -- never SQL, never a computed value."""

    step_type: DeepAnalysisStepType
    column: str | None = Field(default=None, description="Primary column this step analyzes, if applicable")
    secondary_column: str | None = Field(default=None, description="Second column, for correlation/segmentation-by-metric steps")
    reason: str = Field(description="Why this analysis is relevant to this dataset/question")


class DeepAnalysisPlan(BaseModel):
    objective: str
    steps: list[DeepAnalysisStep]


# ====================================================================== #
# 5/6. Hypotheses
# ====================================================================== #

HypothesisPriority = Literal["low", "medium", "high"]
HypothesisStatus = Literal["supported", "rejected", "inconclusive"]


class Hypothesis(BaseModel):
    statement: str = Field(description="A specific, testable claim about the dataset")
    evidence_required: list[str] = Field(description="What data/computation would confirm or refute this")
    analytical_method: DeepAnalysisStepType = Field(
        description="Which deterministic analysis type tests this hypothesis"
    )
    column: str | None = Field(default=None, description="Primary column this hypothesis concerns")
    secondary_column: str | None = Field(default=None)
    priority: HypothesisPriority = "medium"


class HypothesisList(BaseModel):
    """Structured-output wrapper: with_structured_output() needs a single
    BaseModel, not a bare list -- same reason AnalysisPlan wraps
    AnalysisStep in schemas/analysis.py."""

    hypotheses: list[Hypothesis]


class HypothesisResult(BaseModel):
    """The outcome of actually testing a Hypothesis (section 6). `status`
    is set DETERMINISTICALLY from the real computed evidence (see
    agent/deep_analysis/nodes.py::_evaluate_hypothesis) -- an LLM is never
    allowed to simply declare a hypothesis true."""

    hypothesis: Hypothesis
    status: HypothesisStatus
    evidence: dict[str, Any] = Field(default_factory=dict, description="The real computed numbers this verdict is based on")
    reasoning: str = Field(description="A plain-language, deterministic explanation of why this status was assigned")


# ====================================================================== #
# 7. Trend detection (deterministic -- see services/statistics_service.py)
# ====================================================================== #

TrendDirection = Literal["increasing", "decreasing", "flat", "volatile"]


class TrendPeriod(BaseModel):
    period: str
    value: float
    absolute_change: float | None = None
    percentage_change: float | None = None


class TrendResult(BaseModel):
    metric_column: str
    date_column: str
    granularity: Literal["day", "week", "month"]
    direction: TrendDirection
    periods: list[TrendPeriod]
    overall_absolute_change: float | None = None
    overall_percentage_change: float | None = None
    accelerating: bool = Field(default=False, description="Whether period-over-period growth is itself increasing")
    sudden_changes: list[str] = Field(default_factory=list, description="Periods with an unusually large jump, described")


# ====================================================================== #
# 8. Anomaly detection (deterministic)
# ====================================================================== #

AnomalySeverity = Literal["low", "medium", "high"]


class AnomalyResult(BaseModel):
    metric_column: str
    value: float
    expected_range: str = Field(description="Human-readable bounds this value fell outside of")
    method: Literal["iqr", "zscore"]
    severity: AnomalySeverity
    context: dict[str, Any] = Field(default_factory=dict, description="Row context (other column values) for this anomalous row, when available")


# ====================================================================== #
# 9. Correlation analysis (deterministic)
# ====================================================================== #

CorrelationStrength = Literal["negligible", "weak", "moderate", "strong", "very_strong"]


class CorrelationResult(BaseModel):
    column_a: str
    column_b: str
    coefficient: float = Field(ge=-1.0, le=1.0)
    strength: CorrelationStrength
    direction: Literal["positive", "negative", "none"]
    sample_size: int = Field(ge=0)


# ====================================================================== #
# 10. Segmentation (deterministic)
# ====================================================================== #


class SegmentValue(BaseModel):
    segment: str
    metric_value: float
    row_count: int
    contribution_percentage: float = Field(ge=0.0, le=100.0)
    growth_percentage: float | None = Field(
        default=None, description="Change from the first half to the second half of the date range, when a date column was available"
    )


class SegmentResult(BaseModel):
    dimension_column: str
    metric_column: str
    aggregation: Literal["sum", "avg", "count"]
    segments: list[SegmentValue]


# ====================================================================== #
# 11-13. Insight engine
# ====================================================================== #


class ConfidenceBreakdown(BaseModel):
    """Section 13: confidence is a weighted, explainable combination --
    never a bare number the model invents. See
    agent/deep_analysis/nodes.py::_score_confidence."""

    data_completeness: float = Field(ge=0.0, le=1.0)
    sample_size_adequacy: float = Field(ge=0.0, le=1.0)
    statistical_strength: float = Field(ge=0.0, le=1.0)
    consistency: float = Field(ge=0.0, le=1.0)
    overall: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class InsightNarrative(BaseModel):
    """What the Insight Engine's LLM call is actually allowed to produce:
    narrative text only. `evidence` and `confidence` are never fields
    here -- the calling node (agent/deep_analysis/nodes.py::_build_insight)
    attaches those itself, directly from the deterministic evidence object
    and the deterministic confidence score, so an insight's numbers are
    structurally incapable of being invented by the model (section 12)."""

    title: str
    finding: str
    metric: str | None = None
    comparison: str | None = None
    significance: str | None = None
    implication: str


class Insight(BaseModel):
    title: str
    finding: str
    evidence: dict[str, Any] = Field(description="The real computed values this insight is grounded in -- never invented")
    metric: str | None = None
    comparison: str | None = None
    significance: str | None = None
    confidence: ConfidenceBreakdown
    implication: str
    source_step_type: DeepAnalysisStepType


# ====================================================================== #
# 15/16. Review
# ====================================================================== #


class DeepReviewResult(BaseModel):
    approved: bool
    contradictions: list[str] = Field(default_factory=list, description="Insights that contradict their own cited evidence")
    causal_language_violations: list[str] = Field(
        default_factory=list, description="Insights that imply causation from a correlation"
    )
    unsupported_claims: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


# ====================================================================== #
# 20-22. Report
# ====================================================================== #


class Recommendation(BaseModel):
    """Section 21: fact vs. interpretation vs. recommendation, kept
    structurally separate so a reader (and the reviewer) can tell exactly
    which part is grounded data and which part is judgment."""

    fact: str = Field(description="A specific, evidence-backed statement, e.g. a number or comparison")
    interpretation: str = Field(description="What that fact plausibly means")
    recommendation: str = Field(description="A concrete, specific suggested action -- never generic advice")
    grounded_insight_title: str | None = Field(
        default=None, description="Title of the Insight this recommendation is derived from, if any"
    )


class ReportNarrative(BaseModel):
    """What the Report Generator's LLM call is allowed to produce: the
    executive summary plus fact/interpretation/recommendation triplets.
    Section/structure assembly (which insights/trends/anomalies go where,
    the limitations list) happens deterministically around this in
    agent/deep_analysis/nodes.py::build_report_generator_node -- never
    delegated to the model."""

    executive_summary: str
    recommendations: list[Recommendation]


class DatasetOverview(BaseModel):
    row_count: int
    column_count: int
    numeric_column_count: int
    categorical_column_count: int
    date_column_count: int


class Report(BaseModel):
    executive_summary: str
    dataset_overview: DatasetOverview
    data_quality: list[DataQualityIssue]
    key_findings: list[Insight]
    trends: list[TrendResult]
    anomalies: list[AnomalyResult]
    segment_analysis: list[SegmentResult]
    correlations: list[CorrelationResult]
    recommendations: list[Recommendation]
    limitations: list[str]


# ====================================================================== #
# 23/24. Visualization data contract
# ====================================================================== #

VisualizationType = Literal["line", "bar", "scatter", "histogram", "box", "heatmap", "table"]


class AxisSpec(BaseModel):
    field: str
    label: str | None = None


class VisualizationSpec(BaseModel):
    """Section 24's stable, frontend-independent chart contract --
    deliberately separate from schemas/visualization.py's ChartData (a
    Phase 7 contract already used by the Q&A graphs): that one models
    `x_axis`/`y_axis` as bare column-name strings, this one nests them as
    AxisSpec objects per the Phase 10 spec's own example. Extending
    ChartData's shape retroactively would risk breaking every existing
    Phase 7/8 caller; this is a new, additive contract instead."""

    type: VisualizationType
    title: str
    x_axis: AxisSpec | None = None
    y_axis: AxisSpec | None = None
    data: list[dict[str, Any]]


# ====================================================================== #
# 25. Analysis trace
# ====================================================================== #

TraceStatus = Literal["completed", "skipped", "failed"]


class TraceStep(BaseModel):
    """Section 25: safe execution metadata only -- never chain-of-thought
    or raw prompts."""

    step: str
    status: TraceStatus
    duration_ms: float = Field(ge=0.0)
    result_summary: str


# ====================================================================== #
# 26/27. Cost + budget
# ====================================================================== #

BudgetStatus = Literal["ok", "exceeded"]


class AnalysisBudget(BaseModel):
    """Section 27: bounds the whole run so the graph can never spin
    indefinitely. Every `*_used` counter is incremented by the node that
    actually consumes it; `status` flips to "exceeded" the moment any
    limit is hit, and every routing function downstream checks it."""

    max_steps: int
    max_llm_calls: int
    max_query_executions: int
    max_seconds: float

    steps_used: int = 0
    llm_calls_used: int = 0
    query_executions_used: int = 0
    elapsed_seconds: float = 0.0

    status: BudgetStatus = "ok"
    exceeded_reason: str | None = None


class TokenUsageSummary(BaseModel):
    """Section 26: track cost, never spend it on arithmetic an LLM
    shouldn't be doing."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0


# ====================================================================== #
# 34. Final response
# ====================================================================== #

DeepAnalysisStatus = Literal["completed", "budget_exceeded", "insufficient_data", "failed"]


class DeepAnalysisResponse(BaseModel):
    """Section 34: the stable, frontend-independent final contract for an
    autonomous deep-analysis run."""

    analysis_id: str | None = None
    status: DeepAnalysisStatus
    summary: str

    insights: list[Insight] = Field(default_factory=list)
    trends: list[TrendResult] = Field(default_factory=list)
    anomalies: list[AnomalyResult] = Field(default_factory=list)
    correlations: list[CorrelationResult] = Field(default_factory=list)
    segments: list[SegmentResult] = Field(default_factory=list)
    hypothesis_results: list[HypothesisResult] = Field(default_factory=list)

    recommendations: list[Recommendation] = Field(default_factory=list)
    visualizations: list[VisualizationSpec] = Field(default_factory=list)
    data_quality: list[DataQualityIssue] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    report: Report | None = None
    profile: DatasetProfile | None = None

    trace: list[TraceStep] = Field(default_factory=list)
    budget: AnalysisBudget | None = None
    review: DeepReviewResult | None = None
    review_retry_count: int = 0
