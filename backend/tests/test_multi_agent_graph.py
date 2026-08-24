"""
Full-graph integration tests for the Phase 8 multi-agent graph
(app/agent/graph.py::build_multi_agent_graph), plus focused unit tests
for the deterministic human-approval trigger logic in agent/reviewer.py.

All seven structured-output stages are faked (no real OpenAI calls); the
schema inspection, SQL Analyst subgraph, and chart/synthesis stages run
against a real DuckDB dataset -- same "fake the model calls, keep the
deterministic/DB stages real" split test_workflow_graph.py uses for the
Phase 6/7 graph. These exercise conditional-edge routing end to end:
Planner vs. trivial-plan, the reviewer correction loop capped at
Settings.max_review_retries, and all three human-approval triggers
(low confidence, high cost, retries exhausted).

The interrupt()/Command(resume=...) mechanics themselves, exercised
through the real HTTP endpoints, are covered in test_analysis_agent_api.py;
this file goes one level lower, at the graph object directly.
"""

import uuid

import pandas as pd
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent.graph import build_multi_agent_graph
from app.agent.reviewer import _requires_human_approval, route_after_human_approval, route_after_reviewer
from app.agent.subgraphs.sql_workflow import build_sql_analyst_subgraph
from app.schemas.analysis import (
    AnalysisPlan,
    AnalysisStep,
    AnalyticalFindings,
    ExecutedQuery,
    GeneratedSQL,
    Metric,
    ResultValidation,
    SupervisorDecision,
)
from app.schemas.analysis import AnalysisResponse
from app.schemas.review import ReviewResult
from app.services.duckdb_service import get_duckdb_service
from tests.conftest import FakeStructuredModel

REGION_REVENUE_QUERY = "SELECT region, SUM(revenue) AS total FROM dataset GROUP BY region ORDER BY total DESC LIMIT 1"


@pytest.fixture
def sales_dataset():
    service = get_duckdb_service()
    dataset_id = str(uuid.uuid4())
    dataframe = pd.DataFrame(
        {
            "region": ["Delhi", "Mumbai", "Delhi", "Bangalore"],
            "revenue": [5000.0, 4000.0, 3000.0, 6000.0],
        }
    )
    service.register_dataset(dataset_id, dataframe)
    yield dataset_id
    service.unregister_dataset(dataset_id)


def _supervisor_decision(**overrides) -> SupervisorDecision:
    defaults = dict(
        complexity="simple", requires_planning=False, requires_data_analysis=True,
        is_answerable=True, reasoning="Simple aggregation question.",
    )
    defaults.update(overrides)
    return SupervisorDecision(**defaults)


def _findings(**overrides) -> AnalyticalFindings:
    defaults = dict(
        answer="Bangalore leads.", key_findings=["Bangalore: 6000"],
        metrics=[Metric(name="Top revenue", value="6000")], anomalies=[], confidence=0.9,
    )
    defaults.update(overrides)
    return AnalyticalFindings(**defaults)


def _review(**overrides) -> ReviewResult:
    defaults = dict(approved=True, issues=[], corrections=[], confidence=0.9)
    defaults.update(overrides)
    return ReviewResult(**defaults)


def _initial_state(dataset_id: str, question: str) -> dict:
    return {
        "dataset_id": dataset_id,
        "user_question": question,
        "conversation_context": None,
        "current_agent": None,
        "supervisor_decision": None,
        "dataset_schema": None,
        "analysis_plan": None,
        "current_step_index": 0,
        "completed_steps": [],
        "generated_sql": None,
        "validated_sql": None,
        "executed_queries": [],
        "step_result_validation": None,
        "retry_reason": None,
        "retry_count": 0,
        "findings": None,
        "review": None,
        "review_retry_count": 0,
        "human_approval_required": False,
        "human_approval_reason": None,
        "human_approved": None,
        "human_feedback": None,
        "synthesis_context": None,
        "chart": None,
        "final_response": None,
    }


def _build_graph(**model_overrides):
    defaults = dict(
        supervisor_model=FakeStructuredModel(_supervisor_decision()),
        planning_model=FakeStructuredModel(
            AnalysisPlan(objective="unused unless requires_planning=True", steps=[AnalysisStep(step_id=1, objective="x")])
        ),
        sql_model=FakeStructuredModel(GeneratedSQL(sql=REGION_REVENUE_QUERY)),
        result_model=FakeStructuredModel(ResultValidation(is_sufficient=True, reason="ok", confidence=0.95)),
        data_analyst_model=FakeStructuredModel(_findings()),
        reviewer_model=FakeStructuredModel(_review()),
        final_model=FakeStructuredModel(
            AnalysisResponse(
                answer="Delhi leads.", key_findings=["Delhi: 8000"], metrics=[], sql_used=[], confidence=0.9,
                chart=None, follow_up_suggestions=[],
            )
        ),
    )
    defaults.update(model_overrides)
    return build_multi_agent_graph(checkpointer=InMemorySaver(), **defaults)


# ------------------------------------------------------------------ #
# SQL Analyst subgraph, in isolation
# ------------------------------------------------------------------ #


def test_sql_analyst_subgraph_executes_real_sql(sales_dataset) -> None:
    schema = get_duckdb_service().get_schema(sales_dataset)
    subgraph = build_sql_analyst_subgraph(
        sql_model=FakeStructuredModel(GeneratedSQL(sql=REGION_REVENUE_QUERY)),
        result_model=FakeStructuredModel(ResultValidation(is_sufficient=True, reason="ok", confidence=0.95)),
    )

    state = {
        "dataset_id": sales_dataset,
        "user_question": "Which region has the highest revenue?",
        "dataset_schema": schema,
        "analysis_plan": AnalysisPlan(
            objective="Find the top region.", steps=[AnalysisStep(step_id=1, objective="Sum revenue by region.")]
        ),
        "current_step_index": 0,
        "completed_steps": [],
        "generated_sql": None,
        "validated_sql": None,
        "executed_queries": [],
        "step_result_validation": None,
        "retry_reason": None,
        "retry_count": 0,
    }

    result = subgraph.invoke(state)

    assert len(result["executed_queries"]) == 1
    assert result["executed_queries"][0].rows == [{"region": "Delhi", "total": 8000.0}]  # 5000 + 3000
    assert result["current_step_index"] == 1


# ------------------------------------------------------------------ #
# Full graph: routing
# ------------------------------------------------------------------ #


def test_requires_planning_true_routes_through_real_planner(sales_dataset) -> None:
    seen_planner_calls = []

    class RecordingPlanningModel:
        def invoke(self, messages):
            seen_planner_calls.append(messages)
            return AnalysisPlan(
                objective="Find the top region by revenue.",
                steps=[AnalysisStep(step_id=1, objective="Sum revenue by region.")],
            )

    graph = _build_graph(
        supervisor_model=FakeStructuredModel(_supervisor_decision(requires_planning=True, complexity="moderate")),
        planning_model=RecordingPlanningModel(),
    )
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    result = graph.invoke(_initial_state(sales_dataset, "Which region has the highest revenue?"), config=config)

    assert len(seen_planner_calls) == 1
    assert result["analysis_plan"].objective == "Find the top region by revenue."
    assert result["final_response"] is not None


def test_no_human_approval_when_confident_and_cheap(sales_dataset) -> None:
    graph = _build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    result = graph.invoke(_initial_state(sales_dataset, "Which region has the highest revenue?"), config=config)

    assert graph.get_state(config).next == ()
    assert result["final_response"] is not None
    assert result["human_approval_required"] is False


def test_low_confidence_triggers_human_approval(sales_dataset) -> None:
    graph = _build_graph(reviewer_model=FakeStructuredModel(_review(confidence=0.5)))
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    graph.invoke(_initial_state(sales_dataset, "Which region has the highest revenue?"), config=config)

    state = graph.get_state(config)
    assert state.next == ("human_approval",)
    assert "confidence" in state.tasks[0].interrupts[0].value["reason"].lower()


def test_review_retries_capped_then_escalates_to_human(sales_dataset) -> None:
    """route_after_reviewer allows correction cycles only while
    review_retry_count < Settings.max_review_retries (2, per
    core/config.py). With the Reviewer rejecting every time, this must
    escalate to human_approval after exactly 2 rejections, not loop
    forever and not reject a 3rd time."""

    graph = _build_graph(
        data_analyst_model=FakeStructuredModel(
            responses=[_findings(), _findings(answer="corrected once"), _findings(answer="corrected twice")]
        ),
        reviewer_model=FakeStructuredModel(
            responses=[
                _review(approved=False, issues=["numbers don't match"], corrections=["recheck totals"], confidence=0.9),
                _review(approved=False, issues=["still off"], corrections=["recheck again"], confidence=0.9),
            ]
        ),
    )
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    graph.invoke(_initial_state(sales_dataset, "Which region has the highest revenue?"), config=config)

    state = graph.get_state(config)
    assert state.next == ("human_approval",)
    assert state.values["review_retry_count"] == 2
    reason = state.tasks[0].interrupts[0].value["reason"]
    assert "agreement" in reason.lower() or "retries" in reason.lower() or "correction" in reason.lower()


def test_not_answerable_skips_sql_analyst_entirely(sales_dataset) -> None:
    graph = _build_graph(
        supervisor_model=FakeStructuredModel(
            _supervisor_decision(is_answerable=False, reasoning="No weather data in this dataset.")
        )
    )
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    result = graph.invoke(_initial_state(sales_dataset, "What's the weather?"), config=config)

    assert result["executed_queries"] == []
    assert result["findings"] is None
    assert "weather" in result["final_response"].answer.lower() or "can't answer" in result["final_response"].answer.lower()


def test_human_rejection_replans_and_reaches_final_response(sales_dataset) -> None:
    graph = _build_graph(
        reviewer_model=FakeStructuredModel(responses=[_review(confidence=0.5), _review(confidence=0.9)]),
    )
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    graph.invoke(_initial_state(sales_dataset, "Which region has the highest revenue?"), config=config)
    assert graph.get_state(config).next == ("human_approval",)

    result = graph.invoke(
        Command(resume={"approved": False, "feedback": "Please recheck against raw rows."}), config=config
    )

    assert graph.get_state(config).next == ()
    assert result["final_response"] is not None
    assert result["human_approved"] is False
    assert result["human_feedback"] == "Please recheck against raw rows."


# ------------------------------------------------------------------ #
# _requires_human_approval / route_after_reviewer / route_after_human_approval,
# as pure functions -- fast, no graph execution needed.
# ------------------------------------------------------------------ #


def _base_state(**overrides) -> dict:
    state = {
        "executed_queries": [],
        "review": _review(),
        "review_retry_count": 0,
    }
    state.update(overrides)
    return state


def test_requires_human_approval_low_confidence_trigger() -> None:
    requires, reason = _requires_human_approval(_base_state(review=_review(confidence=0.5)))
    assert requires is True
    assert "confidence" in reason.lower()


def test_requires_human_approval_cost_trigger() -> None:
    queries = [
        ExecutedQuery(step_id=1, sql="SELECT 1", columns=["1"], rows=[{"1": 1}], row_count=1, execution_time_ms=3000.0)
    ]
    requires, reason = _requires_human_approval(_base_state(executed_queries=queries))
    assert requires is True
    assert "cost" in reason.lower() or "ms" in reason.lower()


def test_requires_human_approval_retries_exhausted_trigger() -> None:
    requires, reason = _requires_human_approval(
        _base_state(review=_review(approved=False, confidence=0.9), review_retry_count=2)
    )
    assert requires is True


def test_requires_human_approval_false_when_confident_cheap_and_approved() -> None:
    requires, _reason = _requires_human_approval(_base_state())
    assert requires is False


def test_route_after_reviewer_retries_when_budget_remains() -> None:
    state = _base_state(review=_review(approved=False, confidence=0.9), review_retry_count=0)
    assert route_after_reviewer(state) == "data_analyst"


def test_route_after_reviewer_goes_straight_through_when_approved() -> None:
    state = _base_state(review=_review(approved=True, confidence=0.9), review_retry_count=0)
    assert route_after_reviewer(state) == "generate_final_response"


def test_route_after_human_approval_approved_goes_to_final_response() -> None:
    assert route_after_human_approval({"human_approved": True}) == "generate_final_response"


def test_route_after_human_approval_rejected_goes_to_planner() -> None:
    assert route_after_human_approval({"human_approved": False}) == "planner"
