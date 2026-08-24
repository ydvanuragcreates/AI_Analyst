"""
Pure Pydantic validation tests for AnalysisResponse/Metric.

No OpenAI, no DuckDB, no LangGraph -- these just prove the schema itself
enforces its own constraints (in particular 0 <= confidence <= 1), which
is the backstop that catches a structured-output reply even in the case
OpenAI's own JSON-schema constraint enforcement doesn't (its strict
structured-output mode doesn't support numeric min/max keywords, so
Pydantic-side validation is the real guarantee, not just documentation).
"""

import pytest
from pydantic import ValidationError

from app.schemas.analysis import AnalysisResponse, Metric


def _minimal(**overrides) -> dict:
    defaults = {"answer": "Delhi generated the highest revenue.", "key_findings": ["Delhi leads."], "confidence": 0.9}
    defaults.update(overrides)
    return defaults


# 1. Valid AnalysisResponse
def test_valid_analysis_response() -> None:
    response = AnalysisResponse(**_minimal())

    assert response.answer == "Delhi generated the highest revenue."
    assert response.metrics == []
    assert response.sql_used == []
    assert response.chart is None
    assert response.follow_up_suggestions == []


@pytest.mark.parametrize("confidence", [0.0, 0.5, 0.95, 1.0])
def test_confidence_boundaries_are_valid(confidence: float) -> None:
    response = AnalysisResponse(**_minimal(confidence=confidence))

    assert response.confidence == confidence


# 2. Invalid confidence > 1
def test_confidence_above_one_is_rejected() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        AnalysisResponse(**_minimal(confidence=1.2))


# 3. Invalid confidence < 0
def test_confidence_below_zero_is_rejected() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        AnalysisResponse(**_minimal(confidence=-0.2))


# 4. Missing answer
def test_missing_answer_is_rejected() -> None:
    payload = _minimal()
    del payload["answer"]

    with pytest.raises(ValidationError, match="answer"):
        AnalysisResponse(**payload)


# 5. Missing key_findings
def test_missing_key_findings_is_rejected() -> None:
    payload = _minimal()
    del payload["key_findings"]

    with pytest.raises(ValidationError, match="key_findings"):
        AnalysisResponse(**payload)


# 6. Multiple metrics
def test_multiple_metrics_are_accepted() -> None:
    response = AnalysisResponse(
        **_minimal(
            metrics=[
                {"name": "Highest Revenue", "value": "125000", "description": "Delhi's total revenue"},
                {"name": "Lowest Revenue", "value": "76000", "description": "Chennai's total revenue"},
            ]
        )
    )

    assert len(response.metrics) == 2
    assert all(isinstance(m, Metric) for m in response.metrics)
    assert response.metrics[0].name == "Highest Revenue"


def test_metric_description_is_optional() -> None:
    metric = Metric(name="Revenue", value="125000")

    assert metric.description is None


# 7. SQL included
def test_sql_used_can_be_present() -> None:
    response = AnalysisResponse(
        **_minimal(sql_used=["SELECT region, SUM(revenue) FROM dataset GROUP BY region"])
    )

    assert response.sql_used == ["SELECT region, SUM(revenue) FROM dataset GROUP BY region"]


def test_sql_used_can_hold_multiple_queries() -> None:
    # Phase 7: a multi-step analysis can execute more than one query.
    response = AnalysisResponse(**_minimal(sql_used=["SELECT 1", "SELECT 2"]))

    assert response.sql_used == ["SELECT 1", "SELECT 2"]


# 8. No SQL when no query was executed
def test_sql_used_defaults_to_empty_list() -> None:
    response = AnalysisResponse(**_minimal())

    assert response.sql_used == []


def test_empty_metrics_list_for_insufficient_information() -> None:
    response = AnalysisResponse(
        answer="The dataset does not contain enough information to calculate profit margin.",
        key_findings=["Revenue is available.", "Cost or profit information is not available."],
        metrics=[],
        sql_used=[],
        confidence=0.98,
    )

    assert response.metrics == []
    assert response.sql_used == []


def test_follow_up_suggestions_capped_at_three() -> None:
    with pytest.raises(ValidationError):
        AnalysisResponse(**_minimal(follow_up_suggestions=["a", "b", "c", "d"]))


def test_chart_can_be_present() -> None:
    from app.schemas.visualization import ChartData

    chart = ChartData(chart_type="bar", title="Revenue by Region", x_axis="region", y_axis="revenue", data=[{"region": "Delhi", "revenue": 125000}])
    response = AnalysisResponse(**_minimal(chart=chart))

    assert response.chart.chart_type == "bar"
    assert response.chart.data == [{"region": "Delhi", "revenue": 125000}]
