"""
Full-graph integration tests for the Phase 10 deep-analysis graph
(agent/deep_analysis/graph.py::build_deep_analysis_graph), against a real
DuckDB-registered golden dataset. All five structured-output stages
(planner, hypothesis generator, insight engine, reviewer, report
generator) are faked with FakeStructuredModel -- same "fake the model
calls, keep the deterministic/DB stages real" split test_multi_agent_graph.py
uses for the Phase 8 graph. No real OpenAI call happens anywhere here.
"""

import uuid

import pandas as pd
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.deep_analysis.graph import build_deep_analysis_graph
from app.core.config import get_settings
from app.schemas.deep_analysis import (
    DeepAnalysisPlan,
    DeepAnalysisStep,
    DeepReviewResult,
    Hypothesis,
    HypothesisList,
    InsightNarrative,
    Recommendation,
    ReportNarrative,
)
from app.services.duckdb_service import get_duckdb_service
from tests.conftest import FakeStructuredModel


@pytest.fixture
def golden_sales_dataset():
    service = get_duckdb_service()
    dataset_id = str(uuid.uuid4())
    dataframe = pd.read_csv("tests/fixtures/golden_sales.csv")
    service.register_dataset(dataset_id, dataframe)
    yield dataset_id
    service.unregister_dataset(dataset_id)


def _initial_state(dataset_id: str, question: str | None = None) -> dict:
    return {
        "dataset_id": dataset_id,
        "user_question": question,
        "dataset_schema": None,
        "profile": None,
        "data_quality": None,
        "analysis_plan": None,
        "hypotheses": [],
        "hypothesis_results": [],
        "trends": [],
        "anomalies": [],
        "correlations": [],
        "segments": [],
        "insights": [],
        "review": None,
        "review_retry_count": 0,
        "report": None,
        "recommendations": [],
        "visualizations": [],
        "budget": None,
        "trace": [],
        "start_time": 0.0,
        "final_response": None,
    }


_PLAN = DeepAnalysisPlan(
    objective="Investigate sales trends and drivers",
    steps=[
        DeepAnalysisStep(step_type="trend", column="revenue", reason="track revenue over time"),
        DeepAnalysisStep(step_type="correlation", column="discount", secondary_column="revenue", reason="check discount vs revenue"),
        DeepAnalysisStep(step_type="segmentation", column="region", secondary_column="revenue", reason="compare regions"),
        DeepAnalysisStep(step_type="anomaly", column="transaction_amount", reason="check for outliers"),
    ],
)
_HYPOTHESES = HypothesisList(
    hypotheses=[
        Hypothesis(statement="Revenue increased over time", evidence_required=["trend"], analytical_method="trend", column="revenue", priority="high"),
        Hypothesis(statement="North contributes disproportionately to revenue", evidence_required=["segmentation"], analytical_method="segmentation", column="region", secondary_column="revenue", priority="high"),
    ]
)
_INSIGHT_NARRATIVE = InsightNarrative(title="Sample insight", finding="The evidence shows a notable pattern.", implication="Worth investigating further.")
_REVIEW_APPROVED = DeepReviewResult(approved=True, confidence=0.9)
_REPORT = ReportNarrative(
    executive_summary="Overall the dataset shows North dominating revenue with a clear upward trend.",
    recommendations=[Recommendation(fact="North contributes most of total revenue.", interpretation="North is the primary revenue driver.", recommendation="Prioritize North in resource allocation.", grounded_insight_title=None)],
)


def _build_graph(**overrides):
    defaults = dict(
        planning_model=FakeStructuredModel(_PLAN),
        hypothesis_model=FakeStructuredModel(_HYPOTHESES),
        insight_model=FakeStructuredModel(_INSIGHT_NARRATIVE),
        reviewer_model=FakeStructuredModel(_REVIEW_APPROVED),
        report_model=FakeStructuredModel(_REPORT),
        checkpointer=InMemorySaver(),
    )
    defaults.update(overrides)
    return build_deep_analysis_graph(**defaults)


def test_full_pipeline_produces_completed_response_with_all_sections(golden_sales_dataset) -> None:
    graph = _build_graph()
    result = graph.invoke(_initial_state(golden_sales_dataset), config={"configurable": {"thread_id": str(uuid.uuid4())}})
    response = result["final_response"]

    assert response.status == "completed"
    assert response.profile is not None and response.profile.row_count == 13
    assert response.trends and response.anomalies and response.correlations and response.segments
    assert response.insights
    assert response.report is not None
    # All 10 sections from section 20 are present on the assembled Report.
    for field in (
        "executive_summary", "dataset_overview", "data_quality", "key_findings",
        "trends", "anomalies", "segment_analysis", "correlations", "recommendations", "limitations",
    ):
        assert hasattr(response.report, field)
    assert response.limitations  # never empty -- see _build_limitations
    assert response.visualizations
    assert response.trace
    assert response.budget is not None and response.budget.status == "ok"


def test_hypothesis_results_are_grounded_in_real_evidence(golden_sales_dataset) -> None:
    graph = _build_graph()
    result = graph.invoke(_initial_state(golden_sales_dataset), config={"configurable": {"thread_id": str(uuid.uuid4())}})
    response = result["final_response"]

    by_statement = {h.hypothesis.statement: h for h in response.hypothesis_results}
    assert by_statement["Revenue increased over time"].status == "supported"
    assert by_statement["North contributes disproportionately to revenue"].status == "supported"
    # Both must carry real evidence, not an empty dict.
    for hr in response.hypothesis_results:
        assert hr.evidence


def test_no_question_still_produces_a_full_autonomous_analysis(golden_sales_dataset) -> None:
    """Section 44: 'Analyze this dataset and tell me what I should know'
    -- no specific question, the graph must still run end to end."""

    graph = _build_graph()
    result = graph.invoke(_initial_state(golden_sales_dataset, question=None), config={"configurable": {"thread_id": str(uuid.uuid4())}})
    response = result["final_response"]
    assert response.status == "completed"
    assert response.insights


def test_reviewer_rejection_triggers_bounded_retry_then_completes(golden_sales_dataset) -> None:
    settings = get_settings()
    review_sequence = FakeStructuredModel(
        responses=[DeepReviewResult(approved=False, issues=["needs rework"], corrections=["be more specific"], confidence=0.4), _REVIEW_APPROVED]
    )
    graph = _build_graph(reviewer_model=review_sequence)
    result = graph.invoke(_initial_state(golden_sales_dataset), config={"configurable": {"thread_id": str(uuid.uuid4())}})
    response = result["final_response"]

    assert response.review_retry_count == 1
    assert response.review_retry_count <= settings.max_deep_analysis_review_retries
    assert response.review.approved is True


def test_persistent_reviewer_rejection_still_finalizes_within_retry_budget(golden_sales_dataset) -> None:
    """Section 16: MAX_REVIEW_RETRIES must bound the loop -- even a
    reviewer that never approves must not loop forever. The LLM-call
    budget is raised well above what a few retry cycles need so this
    test isolates the retry-count mechanism itself, not budget
    exhaustion (which -- correctly -- also ends the loop, and is
    covered separately by test_budget_exceeded_still_produces_a_graceful_final_response)."""

    settings = get_settings()
    original = settings.max_deep_analysis_llm_calls
    settings.max_deep_analysis_llm_calls = 100
    try:
        always_reject = FakeStructuredModel(DeepReviewResult(approved=False, issues=["still bad"], corrections=["fix it"], confidence=0.3))
        graph = _build_graph(reviewer_model=always_reject)
        result = graph.invoke(_initial_state(golden_sales_dataset), config={"configurable": {"thread_id": str(uuid.uuid4())}})
        response = result["final_response"]

        assert response.review_retry_count == settings.max_deep_analysis_review_retries
        assert response.status == "completed"  # finished, did not hang
    finally:
        settings.max_deep_analysis_llm_calls = original


def test_budget_exceeded_still_produces_a_graceful_final_response(golden_sales_dataset) -> None:
    settings = get_settings()
    original = settings.max_deep_analysis_llm_calls
    settings.max_deep_analysis_llm_calls = 1
    try:
        graph = _build_graph()
        result = graph.invoke(_initial_state(golden_sales_dataset), config={"configurable": {"thread_id": str(uuid.uuid4())}})
        response = result["final_response"]

        assert response.status == "budget_exceeded"
        assert response.summary  # never empty, even when degraded
        assert any("budget" in limitation.lower() for limitation in response.limitations)
    finally:
        settings.max_deep_analysis_llm_calls = original


def test_no_date_column_dataset_skips_trend_gracefully() -> None:
    """Section 38 (adversarial): 'Why did sales fall?' when no date
    exists -- the graph must not attempt (or fail attempting) a trend."""

    service = get_duckdb_service()
    dataset_id = str(uuid.uuid4())
    service.register_dataset(dataset_id, pd.read_csv("tests/fixtures/golden_no_date.csv"))
    try:
        plan_no_trend = DeepAnalysisPlan(
            objective="Investigate product pricing",
            steps=[DeepAnalysisStep(step_type="correlation", column="price", secondary_column="rating", reason="check price vs rating")],
        )
        graph = _build_graph(planning_model=FakeStructuredModel(plan_no_trend), hypothesis_model=FakeStructuredModel(HypothesisList(hypotheses=[])))
        result = graph.invoke(_initial_state(dataset_id), config={"configurable": {"thread_id": str(uuid.uuid4())}})
        response = result["final_response"]

        assert response.status == "completed"
        assert response.trends == []
        assert response.profile.date_columns == []
    finally:
        service.unregister_dataset(dataset_id)


def test_planner_step_referencing_nonexistent_column_is_dropped(golden_sales_dataset) -> None:
    """Never trust the model for column names -- an invented column must
    be filtered out deterministically, not executed against DuckDB."""

    bad_plan = DeepAnalysisPlan(
        objective="x",
        steps=[
            DeepAnalysisStep(step_type="trend", column="profit_margin_that_does_not_exist", reason="invented"),
            DeepAnalysisStep(step_type="trend", column="revenue", reason="real column"),
        ],
    )
    graph = _build_graph(planning_model=FakeStructuredModel(bad_plan), hypothesis_model=FakeStructuredModel(HypothesisList(hypotheses=[])))
    result = graph.invoke(_initial_state(golden_sales_dataset), config={"configurable": {"thread_id": str(uuid.uuid4())}})
    response = result["final_response"]

    assert response.status == "completed"
    assert all(t.metric_column == "revenue" for t in response.trends)
