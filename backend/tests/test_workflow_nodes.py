"""
Unit tests for the individual Phase 6/7 workflow nodes and routing
functions (app/agent/nodes.py), in isolation from the full graph.

LLM-backed nodes use FakeStructuredModel -- no real OpenAI calls.
validate_sql makes no LLM/DuckDB call at all; execute_step_sql is tested
against a real DuckDB-registered dataset, same pattern as
test_duckdb_service.py / test_agent_tools.py. synthesize and
build_chart_data are deterministic (no LLM), tested as plain functions.
"""

import uuid

import pandas as pd
import pytest

from app.agent.nodes import (
    build_analyze_question_node,
    build_chart_data_node,
    build_create_analysis_plan_node,
    build_generate_final_response_node,
    build_generate_step_sql_node,
    build_validate_step_result_node,
    execute_step_sql_node,
    route_after_execute_sql,
    route_after_validate_sql,
    route_after_validate_step_result,
    synthesize_node,
    validate_sql_node,
)
from app.core.config import get_settings
from app.schemas.analysis import (
    AnalysisPlan,
    AnalysisResponse,
    AnalysisStep,
    ConversationContext,
    DatasetSchema,
    ExecutedQuery,
    GeneratedSQL,
    QuestionAnalysis,
    ResultValidation,
    SchemaColumn,
)
from app.services.duckdb_service import get_duckdb_service
from tests.conftest import FakeStructuredModel


@pytest.fixture
def registered_dataset():
    service = get_duckdb_service()
    dataset_id = str(uuid.uuid4())
    dataframe = pd.DataFrame({"region": ["Delhi", "Mumbai", "Delhi"], "revenue": [100.0, 200.0, 50.0]})
    service.register_dataset(dataset_id, dataframe)
    yield dataset_id
    service.unregister_dataset(dataset_id)


def _base_state(dataset_id: str, **overrides) -> dict:
    state = {
        "dataset_id": dataset_id,
        "user_question": "Which region has the highest revenue?",
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
    state.update(overrides)
    return state


_SCHEMA = DatasetSchema(
    dataset_id="x",
    table_name="dataset_x",
    columns=[SchemaColumn(name="region", type="VARCHAR"), SchemaColumn(name="revenue", type="DOUBLE")],
)


def _one_step_plan() -> AnalysisPlan:
    return AnalysisPlan(
        objective="Find the region with the highest total revenue.",
        steps=[
            AnalysisStep(step_id=1, objective="Sum revenue by region and find the max.", required_columns=["region", "revenue"])
        ],
    )


def _two_step_plan() -> AnalysisPlan:
    return AnalysisPlan(
        objective="Compare January and June revenue by region.",
        steps=[
            AnalysisStep(step_id=1, objective="January revenue by region.", required_columns=["region", "revenue"]),
            AnalysisStep(
                step_id=2, objective="June revenue by region and compare to step 1.",
                required_columns=["region", "revenue"], depends_on=[1],
            ),
        ],
    )


# ---------------------------------------------------------------------- #
# 1. Question analysis (+ conversation context, chart suggestion)
# ---------------------------------------------------------------------- #


def test_analyze_question_node_returns_structured_analysis() -> None:
    analysis = QuestionAnalysis(
        intent="aggregation", required_columns=["region", "revenue"], is_answerable=True,
        reasoning="Asks which region has the most revenue.", requires_multiple_steps=False, suggested_chart_type=None,
    )
    node = build_analyze_question_node(FakeStructuredModel(analysis))

    result = node(_base_state("x"))

    assert result["question_analysis"] is analysis


def test_analyze_question_node_includes_conversation_context_when_present() -> None:
    seen_messages = []

    class RecordingModel:
        def invoke(self, messages):
            seen_messages.extend(messages)
            return QuestionAnalysis(
                intent="ranking", required_columns=["region"], is_answerable=True,
                reasoning="follow-up", requires_multiple_steps=False,
            )

    context = ConversationContext(previous_question="Which region has the highest revenue?", previous_answer="Delhi.")
    node = build_analyze_question_node(RecordingModel())

    node(_base_state("x", user_question="What about the second highest?", conversation_context=context))

    joined = " ".join(m.content for m in seen_messages)
    assert "Which region has the highest revenue?" in joined
    assert "Delhi." in joined


# ---------------------------------------------------------------------- #
# 2/3. Complex question -> analysis plan generation (+ step capping)
# ---------------------------------------------------------------------- #


def test_create_analysis_plan_node_returns_plan() -> None:
    plan = _one_step_plan()
    node = build_create_analysis_plan_node(FakeStructuredModel(plan))
    state = _base_state("x", dataset_schema=_SCHEMA, question_analysis=None)

    result = node(state)

    assert result["analysis_plan"] is plan
    assert result["current_step_index"] == 0
    assert result["completed_steps"] == []


def test_create_analysis_plan_node_caps_at_max_analysis_steps() -> None:
    oversized_plan = AnalysisPlan(
        objective="A plan with too many steps.",
        steps=[AnalysisStep(step_id=i, objective=f"step {i}") for i in range(1, 8)],  # 7 steps
    )
    node = build_create_analysis_plan_node(FakeStructuredModel(oversized_plan))
    state = _base_state("x", dataset_schema=_SCHEMA)

    result = node(state)

    assert len(result["analysis_plan"].steps) == get_settings().max_analysis_steps


# ---------------------------------------------------------------------- #
# Per-step SQL generation
# ---------------------------------------------------------------------- #


def test_generate_step_sql_node_targets_current_step() -> None:
    seen_messages = []

    class RecordingModel:
        def invoke(self, messages):
            seen_messages.extend(messages)
            return GeneratedSQL(sql="SELECT 1")

    plan = _two_step_plan()
    node = build_generate_step_sql_node(RecordingModel())
    state = _base_state("x", dataset_schema=_SCHEMA, analysis_plan=plan, current_step_index=1)

    node(state)

    joined = " ".join(m.content for m in seen_messages)
    assert "June revenue by region" in joined  # step 2's objective, not step 1's


def test_generate_step_sql_node_includes_dependency_results() -> None:
    seen_messages = []

    class RecordingModel:
        def invoke(self, messages):
            seen_messages.extend(messages)
            return GeneratedSQL(sql="SELECT 1")

    plan = _two_step_plan()
    prior_result = ExecutedQuery(
        step_id=1, sql="SELECT region, SUM(revenue) FROM dataset", columns=["region", "revenue"],
        rows=[{"region": "Delhi", "revenue": 100000}], row_count=1, execution_time_ms=1.0,
    )
    node = build_generate_step_sql_node(RecordingModel())
    state = _base_state(
        "x", dataset_schema=_SCHEMA, analysis_plan=plan, current_step_index=1, executed_queries=[prior_result]
    )

    node(state)

    joined = " ".join(m.content for m in seen_messages)
    assert "100000" in joined  # step 1's real result reached step 2's prompt


def test_generate_step_sql_node_increments_retry_count_and_resets_state() -> None:
    node = build_generate_step_sql_node(FakeStructuredModel(GeneratedSQL(sql="SELECT * FROM dataset")))
    state = _base_state(
        "x", dataset_schema=_SCHEMA, analysis_plan=_one_step_plan(), retry_count=0,
        validated_sql="stale", retry_reason="stale reason",
    )

    result = node(state)

    assert result["generated_sql"] == "SELECT * FROM dataset"
    assert result["retry_count"] == 1
    assert result["validated_sql"] is None
    assert result["retry_reason"] is None


# ---------------------------------------------------------------------- #
# SQL validation (unchanged from Phase 6) + routing
# ---------------------------------------------------------------------- #


def test_validate_sql_node_accepts_safe_sql() -> None:
    state = _base_state("x", generated_sql="SELECT region, SUM(revenue) FROM dataset GROUP BY region")

    result = validate_sql_node(state)

    assert result["validated_sql"] == state["generated_sql"]


def test_validate_sql_node_rejects_unsafe_sql() -> None:
    result = validate_sql_node(_base_state("x", generated_sql="DROP TABLE dataset"))

    assert "retry_reason" in result
    assert "validated_sql" not in result


def test_route_after_validate_sql_valid_goes_to_execute() -> None:
    assert route_after_validate_sql(_base_state("x", validated_sql="SELECT 1")) == "execute_step_sql"


def test_route_after_validate_sql_invalid_with_retries_left_goes_to_generate() -> None:
    state = _base_state("x", retry_reason="bad SQL", retry_count=1)
    assert route_after_validate_sql(state) == "generate_step_sql"


def test_route_after_validate_sql_invalid_at_retry_limit_goes_to_synthesize() -> None:
    max_retries = get_settings().max_sql_retries
    state = _base_state("x", retry_reason="still bad", retry_count=max_retries)
    assert route_after_validate_sql(state) == "synthesize"


# ---------------------------------------------------------------------- #
# 7/8. Step execution (real DuckDB) + routing
# ---------------------------------------------------------------------- #


def test_execute_step_sql_node_appends_to_executed_queries(registered_dataset) -> None:
    plan = _one_step_plan()
    state = _base_state(
        registered_dataset,
        analysis_plan=plan,
        validated_sql="SELECT region, SUM(revenue) AS total FROM dataset GROUP BY region",
    )

    result = execute_step_sql_node(state)

    assert len(result["executed_queries"]) == 1
    assert result["executed_queries"][0].step_id == 1
    assert result["executed_queries"][0].row_count == 2
    assert result.get("retry_reason") is None


def test_execute_step_sql_node_preserves_earlier_steps(registered_dataset) -> None:
    plan = _two_step_plan()
    earlier = ExecutedQuery(
        step_id=1, sql="SELECT 1", columns=["a"], rows=[{"a": 1}], row_count=1, execution_time_ms=1.0
    )
    state = _base_state(
        registered_dataset, analysis_plan=plan, current_step_index=1, executed_queries=[earlier],
        validated_sql="SELECT region, SUM(revenue) AS total FROM dataset GROUP BY region",
    )

    result = execute_step_sql_node(state)

    assert len(result["executed_queries"]) == 2
    assert result["executed_queries"][0].step_id == 1
    assert result["executed_queries"][1].step_id == 2


def test_execute_step_sql_node_captures_execution_failure(registered_dataset) -> None:
    state = _base_state(registered_dataset, analysis_plan=_one_step_plan(), validated_sql="SELECT nonexistent FROM dataset")

    result = execute_step_sql_node(state)

    assert "retry_reason" in result
    assert "executed_queries" not in result


def test_route_after_execute_sql_success_goes_to_validate_step_result() -> None:
    assert route_after_execute_sql(_base_state("x")) == "validate_step_result"


def test_route_after_execute_sql_failure_with_retries_left_goes_to_generate() -> None:
    state = _base_state("x", retry_reason="execution failed", retry_count=1)
    assert route_after_execute_sql(state) == "generate_step_sql"


def test_route_after_execute_sql_failure_at_retry_limit_goes_to_synthesize() -> None:
    max_retries = get_settings().max_sql_retries
    state = _base_state("x", retry_reason="execution failed", retry_count=max_retries)
    assert route_after_execute_sql(state) == "synthesize"


# ---------------------------------------------------------------------- #
# Step result validation (advance vs retry) + routing
# ---------------------------------------------------------------------- #


def test_validate_step_result_node_sufficient_advances_to_next_step() -> None:
    validation = ResultValidation(is_sufficient=True, reason="Clear.", confidence=0.95)
    node = build_validate_step_result_node(FakeStructuredModel(validation))
    plan = _two_step_plan()
    query = ExecutedQuery(step_id=1, sql="SELECT 1", columns=["a"], rows=[{"a": 1}], row_count=1, execution_time_ms=1.0)
    state = _base_state("x", analysis_plan=plan, current_step_index=0, executed_queries=[query], retry_count=2)

    result = node(state)

    assert result["completed_steps"] == [1]
    assert result["current_step_index"] == 1
    assert result["retry_count"] == 0  # fresh budget for step 2
    assert result["generated_sql"] is None


def test_validate_step_result_node_insufficient_sets_retry_reason() -> None:
    validation = ResultValidation(
        is_sufficient=False, reason="Empty result.", missing_information=["any matching rows"], confidence=0.9
    )
    node = build_validate_step_result_node(FakeStructuredModel(validation))
    plan = _one_step_plan()
    query = ExecutedQuery(step_id=1, sql="SELECT 1", columns=["a"], rows=[], row_count=0, execution_time_ms=1.0)
    state = _base_state("x", analysis_plan=plan, executed_queries=[query])

    result = node(state)

    assert "retry_reason" in result
    assert "current_step_index" not in result  # did not advance


def test_route_after_validate_step_result_sufficient_with_more_steps_goes_to_generate() -> None:
    plan = _two_step_plan()
    validation = ResultValidation(is_sufficient=True, reason="ok", confidence=0.9)
    state = _base_state("x", analysis_plan=plan, current_step_index=1, step_result_validation=validation)

    assert route_after_validate_step_result(state) == "generate_step_sql"


def test_route_after_validate_step_result_sufficient_no_more_steps_goes_to_synthesize() -> None:
    plan = _one_step_plan()
    validation = ResultValidation(is_sufficient=True, reason="ok", confidence=0.9)
    state = _base_state("x", analysis_plan=plan, current_step_index=1, step_result_validation=validation)

    assert route_after_validate_step_result(state) == "synthesize"


def test_route_after_validate_step_result_insufficient_with_retries_left_goes_to_generate() -> None:
    validation = ResultValidation(is_sufficient=False, reason="not enough", confidence=0.5)
    state = _base_state("x", analysis_plan=_one_step_plan(), step_result_validation=validation, retry_count=1)

    assert route_after_validate_step_result(state) == "generate_step_sql"


def test_route_after_validate_step_result_insufficient_at_retry_limit_goes_to_synthesize() -> None:
    max_retries = get_settings().max_sql_retries
    validation = ResultValidation(is_sufficient=False, reason="not enough", confidence=0.5)
    state = _base_state("x", analysis_plan=_one_step_plan(), step_result_validation=validation, retry_count=max_retries)

    assert route_after_validate_step_result(state) == "synthesize"


# ---------------------------------------------------------------------- #
# 9. Synthesis (deterministic)
# ---------------------------------------------------------------------- #


def test_synthesize_node_formats_all_executed_queries() -> None:
    plan = _two_step_plan()
    queries = [
        ExecutedQuery(step_id=1, sql="SQL1", columns=["region"], rows=[{"region": "Delhi"}], row_count=1, execution_time_ms=1.0),
        ExecutedQuery(step_id=2, sql="SQL2", columns=["region"], rows=[{"region": "Mumbai"}], row_count=1, execution_time_ms=1.0),
    ]
    state = _base_state("x", analysis_plan=plan, executed_queries=queries)

    result = synthesize_node(state)

    assert "January revenue by region" in result["synthesis_context"]
    assert "June revenue by region" in result["synthesis_context"]
    assert "Delhi" in result["synthesis_context"]
    assert "Mumbai" in result["synthesis_context"]


def test_synthesize_node_handles_no_successful_queries() -> None:
    result = synthesize_node(_base_state("x", analysis_plan=_one_step_plan(), executed_queries=[]))

    assert "No step executed successfully" in result["synthesis_context"]


# ---------------------------------------------------------------------- #
# 12/13. Chart generation (deterministic, from real data)
# ---------------------------------------------------------------------- #


def test_build_chart_data_node_builds_chart_from_real_query_result() -> None:
    analysis = QuestionAnalysis(
        intent="comparison", required_columns=["region"], is_answerable=True, reasoning="x",
        requires_multiple_steps=False, suggested_chart_type="bar",
    )
    query = ExecutedQuery(
        step_id=1, sql="SELECT region, SUM(revenue) AS total FROM dataset GROUP BY region",
        columns=["region", "total"], rows=[{"region": "Delhi", "total": 150.0}, {"region": "Mumbai", "total": 200.0}],
        row_count=2, execution_time_ms=1.0,
    )
    state = _base_state("x", question_analysis=analysis, analysis_plan=_one_step_plan(), executed_queries=[query])

    result = build_chart_data_node(state)

    assert result["chart"].chart_type == "bar"
    assert result["chart"].x_axis == "region"
    assert result["chart"].y_axis == "total"
    # The chart's data is EXACTLY the real query rows -- never invented.
    assert result["chart"].data == query.rows


def test_build_chart_data_node_returns_none_when_no_chart_type_suggested() -> None:
    analysis = QuestionAnalysis(
        intent="lookup", required_columns=["revenue"], is_answerable=True, reasoning="x",
        requires_multiple_steps=False, suggested_chart_type=None,
    )
    query = ExecutedQuery(
        step_id=1, sql="SELECT SUM(revenue) AS total FROM dataset", columns=["total"],
        rows=[{"total": 350.0}], row_count=1, execution_time_ms=1.0,
    )
    state = _base_state("x", question_analysis=analysis, executed_queries=[query])

    result = build_chart_data_node(state)

    assert result["chart"] is None


def test_build_chart_data_node_returns_none_for_single_column_scalar_result() -> None:
    analysis = QuestionAnalysis(
        intent="aggregation", required_columns=["revenue"], is_answerable=True, reasoning="x",
        requires_multiple_steps=False, suggested_chart_type="bar",  # model suggests a chart...
    )
    query = ExecutedQuery(
        step_id=1, sql="SELECT SUM(revenue) AS total FROM dataset", columns=["total"],
        rows=[{"total": 350.0}], row_count=1, execution_time_ms=1.0,
    )
    state = _base_state("x", question_analysis=analysis, executed_queries=[query])

    result = build_chart_data_node(state)

    # ...but the node overrides it: a single-column scalar has no shape to chart.
    assert result["chart"] is None


def test_build_chart_data_node_returns_none_when_no_queries_executed() -> None:
    analysis = QuestionAnalysis(
        intent="aggregation", required_columns=[], is_answerable=True, reasoning="x",
        requires_multiple_steps=False, suggested_chart_type="bar",
    )
    result = build_chart_data_node(_base_state("x", question_analysis=analysis, executed_queries=[]))

    assert result["chart"] is None


# ---------------------------------------------------------------------- #
# 17/18. Final response: sql_used tracking + conversation context update
# ---------------------------------------------------------------------- #


def test_generate_final_response_node_lists_all_executed_sql() -> None:
    response = AnalysisResponse(
        answer="Delhi grew the most.", key_findings=["Delhi grew 40%."], confidence=0.9,
        sql_used=["THIS SHOULD BE OVERWRITTEN"],
    )
    node = build_generate_final_response_node(FakeStructuredModel(response))
    queries = [
        ExecutedQuery(step_id=1, sql="SQL 1", columns=["a"], rows=[], row_count=0, execution_time_ms=1.0),
        ExecutedQuery(step_id=2, sql="SQL 2", columns=["a"], rows=[], row_count=0, execution_time_ms=1.0),
    ]
    state = _base_state("x", executed_queries=queries, analysis_plan=_two_step_plan())

    result = node(state)

    assert result["final_response"].sql_used == ["SQL 1", "SQL 2"]


def test_generate_final_response_node_updates_conversation_context() -> None:
    response = AnalysisResponse(
        answer="Delhi has the highest revenue.", key_findings=["Delhi leads.", "Mumbai is second."], confidence=0.9
    )
    node = build_generate_final_response_node(FakeStructuredModel(response))
    state = _base_state("x", user_question="Which region has the highest revenue?")

    result = node(state)

    context = result["conversation_context"]
    assert context.previous_question == "Which region has the highest revenue?"
    assert context.previous_answer == "Delhi has the highest revenue."
    assert context.relevant_findings == ["Delhi leads.", "Mumbai is second."]


def test_generate_final_response_node_merges_deterministic_chart() -> None:
    from app.schemas.visualization import ChartData

    response = AnalysisResponse(answer="x", key_findings=["x"], confidence=0.9)
    chart = ChartData(chart_type="bar", title="Revenue", x_axis="region", y_axis="revenue", data=[{"region": "Delhi", "revenue": 100}])
    node = build_generate_final_response_node(FakeStructuredModel(response))
    state = _base_state("x", chart=chart)

    result = node(state)

    assert result["final_response"].chart is chart
