"""
LangGraph state for the Phase 10 deep-analysis graph.

A new TypedDict, DeepAnalysisState, rather than extending MultiAgentState
(Phase 8) -- same reasoning agent/state.py's module docstring gives for
keeping AgentState/WorkflowState/MultiAgentState separate: this graph is
structurally a different pipeline (profile -> quality -> plan ->
hypotheses -> deterministic analysis -> insights -> review -> report),
not a variant of the Q&A supervisor/planner/SQL-analyst/reviewer flow.
Bolting ~15 new fields onto MultiAgentState would make neither graph's
actual data contract clear, and every node reused unchanged from Phase
6-8 (none are, here -- deep analysis needs its own node logic throughout)
would be the only justification for that, per the same file's own
precedent.
"""

from typing import TypedDict

from app.schemas.analysis import DatasetSchema
from app.schemas.deep_analysis import (
    AnalysisBudget,
    AnomalyResult,
    CorrelationResult,
    DataQualityReport,
    DatasetProfile,
    DeepAnalysisPlan,
    DeepAnalysisResponse,
    DeepReviewResult,
    Hypothesis,
    HypothesisResult,
    Insight,
    Recommendation,
    Report,
    SegmentResult,
    TraceStep,
    TrendResult,
    VisualizationSpec,
)


class DeepAnalysisState(TypedDict):
    """State passed between nodes in the Phase 10 deep-analysis graph.

    - dataset_id: set at invocation, read by every node.
    - user_question: optional -- "analyze this dataset and tell me what I
      should know" (section 44) needs no specific question; when absent,
      the Planner treats the objective as "autonomously investigate this
      dataset" rather than grounding against a question that doesn't
      exist.
    - dataset_schema / profile / data_quality: built once, early, by
      deterministic nodes -- never re-derived later (section 40).
    - analysis_plan: the Planner's structured selection of WHICH
      deterministic analyses are relevant to this dataset's actual shape
      (section 4/28) -- not SQL, not a computed value.
    - hypotheses / hypothesis_results: generated once, tested exactly
      once each by analysis_executor_node against real computed evidence
      (section 6) -- hypothesis_results is 1:1 with hypotheses that were
      actually reachable within budget.
    - trends / anomalies / correlations / segments: the real, deterministic
      output of statistics_service, grown by analysis_executor_node as it
      works through the plan. Every Insight's `evidence` field is built
      FROM one of these objects, never independently.
    - insights: evidence-first, confidence-scored, reviewed findings
      (sections 11-14) -- re-generated in place on a reviewer rejection
      cycle (review_retry_count tracks how many).
    - review / review_retry_count: the Reviewer's judgment and how many
      Insight-Engine <-> Reviewer correction cycles have run -- same
      pattern as MultiAgentState.review/review_retry_count.
    - report / recommendations / visualizations: the final, assembled
      deliverables -- report's sections are built deterministically
      around an LLM-written executive_summary/recommendations narrative
      (see nodes.py::build_report_generator_node).
    - budget / trace: section 25 (safe execution metadata, no
      chain-of-thought) and section 27 (bounded run) -- trace grows via
      explicit read-modify-write like WorkflowState.executed_queries;
      budget is replaced wholesale each update (a small, cheap Pydantic
      object, "last write wins" like every other single-value field here).
    - start_time: perf_counter() timestamp set once by dataset_context_node,
      used to compute budget.elapsed_seconds without re-reading the clock
      from scratch each time (there is no "elapsed so far" without a
      fixed start).
    - final_response: set only once, by the last node before END.
    """

    dataset_id: str
    user_question: str | None

    dataset_schema: DatasetSchema | None
    profile: DatasetProfile | None
    data_quality: DataQualityReport | None

    analysis_plan: DeepAnalysisPlan | None
    hypotheses: list[Hypothesis]
    hypothesis_results: list[HypothesisResult]

    trends: list[TrendResult]
    anomalies: list[AnomalyResult]
    correlations: list[CorrelationResult]
    segments: list[SegmentResult]

    insights: list[Insight]
    review: DeepReviewResult | None
    review_retry_count: int

    report: Report | None
    recommendations: list[Recommendation]
    visualizations: list[VisualizationSpec]

    budget: AnalysisBudget
    trace: list[TraceStep]
    start_time: float

    final_response: DeepAnalysisResponse | None
