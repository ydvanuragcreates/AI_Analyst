"""
Full-graph integration tests for the deterministic multi-step analysis
workflow (app/agent/graph.py::build_analysis_workflow_graph).

All five structured-output stages are faked (no real OpenAI calls); the
schema inspection and SQL execution stages run against a real DuckDB
dataset. These exercise the actual conditional-edge routing end to end
-- single-step, multi-step, retries, chart generation, and conversation
continuity via the graph's own checkpointer.
"""

import uuid

import pandas as pd
import pytest

from app.agent.graph import build_analysis_workflow_graph
from app.core.config import get_settings
from app.schemas.analysis import (
    AnalysisPlan,
    AnalysisResponse,
    AnalysisStep,
    GeneratedSQL,
    QuestionAnalysis,
    ResultValidation,
)
from app.services.duckdb_service import get_duckdb_service
from tests.conftest import FakeStructuredModel

REGION_REVENUE_QUERY = "SELECT region, SUM(revenue) AS total FROM dataset GROUP BY region ORDER BY total DESC LIMIT 1"


@pytest.fixture
def sales_dataset():
    """Matches the spec's example: date/region/product/quantity/revenue."""

    service = get_duckdb_service()
    dataset_id = str(uuid.uuid4())
    dataframe = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
            "region": ["Delhi", "Mumbai", "Delhi", "Bangalore"],
            "product": ["Widget", "Widget", "Gadget", "Widget"],
            "quantity": [10, 8, 5, 12],
            "revenue": [5000.0, 4000.0, 3000.0, 6000.0],
        }
    )
    service.register_dataset(dataset_id, dataframe)
    yield dataset_id
    service.unregister_dataset(dataset_id)


def _question_analysis(**overrides) -> QuestionAnalysis:
    defaults = dict(
        intent="aggregation", required_columns=["region", "revenue"], operation="SUM", group_by="region",
        sort="DESC", limit=1, is_answerable=True, reasoning="Asks which region has the most revenue.",
        requires_multiple_steps=False, suggested_chart_type=None,
    )
    defaults.update(overrides)
    return QuestionAnalysis(**defaults)


def _one_step_plan() -> AnalysisPlan:
    return AnalysisPlan(
        objective="Find the region with the highest total revenue.",
        steps=[AnalysisStep(step_id=1, objective="Sum revenue by region.", required_columns=["region", "revenue"])],
    )


def _initial_state(dataset_id: str, question: str) -> dict:
    return {
        "dataset_id": dataset_id,
        "user_question": question,
        "conversation_context": None,
        "question_analysis": None,
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
        "synthesis_context": None,
        "chart": None,
        "final_response": None,
    }


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


# ---------------------------------------------------------------------- #
# 1/4/16/17/18. Simple, single-step workflow -- no unnecessary multi-step plan
# ---------------------------------------------------------------------- #


def test_single_step_workflow_for_simple_question(sales_dataset) -> None:
    graph = build_analysis_workflow_graph(
        question_model=FakeStructuredModel(_question_analysis()),
        planning_model=FakeStructuredModel(_one_step_plan()),
        sql_model=FakeStructuredModel(GeneratedSQL(sql=REGION_REVENUE_QUERY)),
        result_model=FakeStructuredModel(ResultValidation(is_sufficient=True, reason="Direct answer.", confidence=0.96)),
        final_model=FakeStructuredModel(
            AnalysisResponse(answer="Bangalore generated the highest revenue.", key_findings=["Bangalore leads."], confidence=0.95)
        ),
    )

    result = graph.invoke(
        _initial_state(sales_dataset, "What region generated the highest revenue?"), config=_config("t1")
    )

    final = result["final_response"]
    assert isinstance(final, AnalysisResponse)
    assert final.sql_used == [REGION_REVENUE_QUERY]  # 17. actual SQL tracking
    assert len(result["executed_queries"]) == 1
    assert result["retry_count"] == 1  # single attempt, no retry needed


# ---------------------------------------------------------------------- #
# 2/5/8/9. Complex, multi-step workflow with a dependency between steps
# ---------------------------------------------------------------------- #


def test_multi_step_workflow_with_dependent_steps(sales_dataset) -> None:
    plan = AnalysisPlan(
        objective="Compare Delhi and Mumbai revenue and report the difference.",
        steps=[
            AnalysisStep(step_id=1, objective="Delhi total revenue.", required_columns=["region", "revenue"]),
            AnalysisStep(
                step_id=2, objective="Mumbai total revenue, then compare to Delhi.",
                required_columns=["region", "revenue"], depends_on=[1],
            ),
        ],
    )
    step_sqls = [
        GeneratedSQL(sql="SELECT SUM(revenue) AS total FROM dataset WHERE region = 'Delhi'"),
        GeneratedSQL(sql="SELECT SUM(revenue) AS total FROM dataset WHERE region = 'Mumbai'"),
    ]

    graph = build_analysis_workflow_graph(
        question_model=FakeStructuredModel(_question_analysis(intent="comparison", requires_multiple_steps=True)),
        planning_model=FakeStructuredModel(plan),
        sql_model=FakeStructuredModel(responses=step_sqls),
        result_model=FakeStructuredModel(ResultValidation(is_sufficient=True, reason="ok", confidence=0.9)),
        final_model=FakeStructuredModel(
            AnalysisResponse(answer="Delhi generated more revenue than Mumbai.", key_findings=["Delhi: 8000", "Mumbai: 4000"], confidence=0.9)
        ),
    )

    result = graph.invoke(_initial_state(sales_dataset, "Compare Delhi and Mumbai revenue."), config=_config("t2"))

    # 8. multiple query results, both real and both distinctly tracked.
    assert len(result["executed_queries"]) == 2
    assert result["executed_queries"][0].step_id == 1
    assert result["executed_queries"][1].step_id == 2
    # 9. synthesis saw both steps, grouped and labeled.
    assert "Delhi total revenue" in result["synthesis_context"]
    assert "Mumbai total revenue" in result["synthesis_context"]
    # 17. every actually-executed query is tracked, in order.
    assert result["final_response"].sql_used == [s.sql for s in step_sqls]
    assert result["completed_steps"] == [1, 2]


# ---------------------------------------------------------------------- #
# Missing column recovery (retry within a step)
# ---------------------------------------------------------------------- #


def test_missing_column_recovers_on_retry(sales_dataset) -> None:
    bad_sql = "SELECT region, SUM(sales_amount) AS total FROM dataset GROUP BY region ORDER BY total DESC LIMIT 1"

    graph = build_analysis_workflow_graph(
        question_model=FakeStructuredModel(_question_analysis()),
        planning_model=FakeStructuredModel(_one_step_plan()),
        sql_model=FakeStructuredModel(responses=[GeneratedSQL(sql=bad_sql), GeneratedSQL(sql=REGION_REVENUE_QUERY)]),
        result_model=FakeStructuredModel(ResultValidation(is_sufficient=True, reason="ok", confidence=0.95)),
        final_model=FakeStructuredModel(AnalysisResponse(answer="Bangalore leads.", key_findings=["x"], confidence=0.9)),
    )

    result = graph.invoke(_initial_state(sales_dataset, "highest revenue region?"), config=_config("t3"))

    assert result["retry_count"] == 2  # attempt 1 failed, attempt 2 succeeded
    assert result["final_response"].sql_used == [REGION_REVENUE_QUERY]


# ---------------------------------------------------------------------- #
# 14. Empty result handling
# ---------------------------------------------------------------------- #


def test_empty_result_is_retried_then_recovers(sales_dataset) -> None:
    empty_query = "SELECT region FROM dataset WHERE region = 'Nonexistent'"

    graph = build_analysis_workflow_graph(
        question_model=FakeStructuredModel(_question_analysis()),
        planning_model=FakeStructuredModel(_one_step_plan()),
        sql_model=FakeStructuredModel(responses=[GeneratedSQL(sql=empty_query), GeneratedSQL(sql=REGION_REVENUE_QUERY)]),
        result_model=FakeStructuredModel(
            responses=[
                ResultValidation(is_sufficient=False, reason="Empty result.", missing_information=["any rows"], confidence=0.9),
                ResultValidation(is_sufficient=True, reason="Now has an answer.", confidence=0.95),
            ]
        ),
        final_model=FakeStructuredModel(AnalysisResponse(answer="Bangalore leads.", key_findings=["x"], confidence=0.9)),
    )

    result = graph.invoke(_initial_state(sales_dataset, "highest revenue region?"), config=_config("t4"))

    assert result["executed_queries"][-1].row_count == 1
    assert result["final_response"].sql_used[-1] == REGION_REVENUE_QUERY


# ---------------------------------------------------------------------- #
# 15. Multi-step failure -- insufficient data even after retries
# ---------------------------------------------------------------------- #


def test_insufficient_data_handled_gracefully_after_retries_exhausted(sales_dataset) -> None:
    graph = build_analysis_workflow_graph(
        question_model=FakeStructuredModel(
            _question_analysis(intent="aggregation", required_columns=["profit", "revenue"], reasoning="profit margin")
        ),
        planning_model=FakeStructuredModel(
            AnalysisPlan(objective="Attempt profit margin from available columns.", steps=[AnalysisStep(step_id=1, objective="Sum revenue.")])
        ),
        sql_model=FakeStructuredModel(GeneratedSQL(sql="SELECT SUM(revenue) AS total_revenue FROM dataset")),
        result_model=FakeStructuredModel(
            ResultValidation(
                is_sufficient=False, reason="Only revenue is available; no cost/profit data.",
                missing_information=["cost", "profit"], confidence=0.92,
            )
        ),
        final_model=FakeStructuredModel(
            AnalysisResponse(
                answer="The dataset does not contain enough information to calculate profit margin.",
                key_findings=["Revenue is available.", "Cost/profit is not available."], metrics=[], confidence=0.98,
            )
        ),
    )

    result = graph.invoke(_initial_state(sales_dataset, "What is the profit margin?"), config=_config("t5"))

    max_retries = get_settings().max_sql_retries
    assert result["retry_count"] == max_retries  # stopped, no infinite loop
    final = result["final_response"]
    assert final.metrics == []
    assert "does not contain enough information" in final.answer
    # The revenue query genuinely executed on every one of the 3 attempts
    # (it's valid SQL -- only its *result* was judged insufficient each
    # time) -- sql_used honestly reflects every real execution, even
    # though the overall question couldn't be fully answered.
    assert final.sql_used == ["SELECT SUM(revenue) AS total_revenue FROM dataset"] * max_retries


# ---------------------------------------------------------------------- #
# 16. Maximum 5 steps
# ---------------------------------------------------------------------- #


def test_max_retries_stops_after_configured_attempts_no_more(sales_dataset) -> None:
    max_retries = get_settings().max_sql_retries
    call_counter = {"count": 0}

    class CountingUnsafeSQLModel:
        def invoke(self, messages):
            call_counter["count"] += 1
            return GeneratedSQL(sql="DROP TABLE dataset")

    graph = build_analysis_workflow_graph(
        question_model=FakeStructuredModel(_question_analysis()),
        planning_model=FakeStructuredModel(_one_step_plan()),
        sql_model=CountingUnsafeSQLModel(),
        result_model=FakeStructuredModel(ResultValidation(is_sufficient=True, reason="n/a", confidence=0.5)),
        final_model=FakeStructuredModel(
            AnalysisResponse(answer="I was unable to generate a safe query.", key_findings=["Every query was rejected."], confidence=0.4)
        ),
    )

    result = graph.invoke(_initial_state(sales_dataset, "highest revenue region?"), config=_config("t6"))

    assert call_counter["count"] == max_retries
    assert result["retry_count"] == max_retries
    assert result["final_response"].sql_used == []


# ---------------------------------------------------------------------- #
# 12/13. Chart generation
# ---------------------------------------------------------------------- #


def test_chart_generated_for_comparison_question(sales_dataset) -> None:
    query_sql = "SELECT region, SUM(revenue) AS total FROM dataset GROUP BY region"

    graph = build_analysis_workflow_graph(
        question_model=FakeStructuredModel(
            _question_analysis(intent="comparison", reasoning="compare regions", suggested_chart_type="bar")
        ),
        planning_model=FakeStructuredModel(
            AnalysisPlan(objective="Compare revenue across regions.", steps=[AnalysisStep(step_id=1, objective="Revenue by region.")])
        ),
        sql_model=FakeStructuredModel(GeneratedSQL(sql=query_sql)),
        result_model=FakeStructuredModel(ResultValidation(is_sufficient=True, reason="ok", confidence=0.9)),
        final_model=FakeStructuredModel(AnalysisResponse(answer="Bangalore leads.", key_findings=["x"], confidence=0.9)),
    )

    result = graph.invoke(_initial_state(sales_dataset, "Compare revenue across regions."), config=_config("t7"))

    assert result["final_response"].chart is not None
    assert result["final_response"].chart.chart_type == "bar"
    assert result["final_response"].chart.data  # real rows, not empty


def test_no_chart_for_single_value_question(sales_dataset) -> None:
    graph = build_analysis_workflow_graph(
        question_model=FakeStructuredModel(
            _question_analysis(intent="aggregation", reasoning="total revenue", suggested_chart_type=None)
        ),
        planning_model=FakeStructuredModel(
            AnalysisPlan(objective="Total revenue.", steps=[AnalysisStep(step_id=1, objective="Sum all revenue.")])
        ),
        sql_model=FakeStructuredModel(GeneratedSQL(sql="SELECT SUM(revenue) AS total FROM dataset")),
        result_model=FakeStructuredModel(ResultValidation(is_sufficient=True, reason="ok", confidence=0.95)),
        final_model=FakeStructuredModel(AnalysisResponse(answer="Total revenue is 18000.", key_findings=["x"], confidence=0.95)),
    )

    result = graph.invoke(_initial_state(sales_dataset, "What is total revenue?"), config=_config("t8"))

    assert result["final_response"].chart is None


# ---------------------------------------------------------------------- #
# 10/11. Conversational follow-up + thread memory (checkpointer)
# ---------------------------------------------------------------------- #


def test_conversation_follow_up_uses_prior_turn_context(sales_dataset) -> None:
    seen_messages_by_call: list[list] = []

    class RecordingQuestionModel:
        def invoke(self, messages):
            seen_messages_by_call.append(list(messages))
            return _question_analysis()

    graph = build_analysis_workflow_graph(
        question_model=RecordingQuestionModel(),
        planning_model=FakeStructuredModel(_one_step_plan()),
        sql_model=FakeStructuredModel(GeneratedSQL(sql=REGION_REVENUE_QUERY)),
        result_model=FakeStructuredModel(ResultValidation(is_sufficient=True, reason="ok", confidence=0.95)),
        final_model=FakeStructuredModel(
            AnalysisResponse(answer="Bangalore has the highest revenue.", key_findings=["Bangalore: 6000"], confidence=0.95)
        ),
    )
    thread_config = _config("conversation-thread")

    # Turn 1: fresh conversation.
    turn1_state = _initial_state(sales_dataset, "Which region has the highest revenue?")
    result1 = graph.invoke(turn1_state, config=thread_config)
    assert result1["conversation_context"].previous_answer == "Bangalore has the highest revenue."

    # Turn 2: same thread_id, explicitly seeded with turn 1's curated context
    # (mirrors what api/routes/analysis.py does via graph.aget_state()).
    turn2_state = _initial_state(sales_dataset, "What about the second highest?")
    turn2_state["conversation_context"] = result1["conversation_context"]
    graph.invoke(turn2_state, config=thread_config)

    # analyze_question on turn 2 must have SEEN turn 1's question/answer.
    turn2_messages = seen_messages_by_call[1]
    joined = " ".join(m.content for m in turn2_messages)
    assert "Which region has the highest revenue?" in joined
    assert "Bangalore has the highest revenue." in joined


def test_thread_memory_persists_via_checkpointer_get_state(sales_dataset) -> None:
    graph = build_analysis_workflow_graph(
        question_model=FakeStructuredModel(_question_analysis()),
        planning_model=FakeStructuredModel(_one_step_plan()),
        sql_model=FakeStructuredModel(GeneratedSQL(sql=REGION_REVENUE_QUERY)),
        result_model=FakeStructuredModel(ResultValidation(is_sufficient=True, reason="ok", confidence=0.95)),
        final_model=FakeStructuredModel(AnalysisResponse(answer="Bangalore leads.", key_findings=["x"], confidence=0.9)),
    )
    config = _config("persisted-thread")

    graph.invoke(_initial_state(sales_dataset, "highest revenue region?"), config=config)

    checkpointed = graph.get_state(config)
    assert checkpointed.values["conversation_context"].previous_answer == "Bangalore leads."
