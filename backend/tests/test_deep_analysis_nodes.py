"""
Unit tests for the deterministic (non-LLM) logic in
agent/deep_analysis/nodes.py: hypothesis evaluation, confidence scoring,
contradiction/causal-language detection, and budget enforcement. No LLM
call, no DuckDB call -- pure functions against hand-built Pydantic
objects, so these run instantly and never flake.
"""

from app.agent.deep_analysis.nodes import (
    _consume,
    _detect_causal_language,
    _detect_contradictions,
    _evaluate_hypothesis,
    _init_budget,
    _insight_priority_score,
    _score_confidence,
    _task_key,
)
from app.core.config import get_settings
from app.schemas.deep_analysis import (
    ConfidenceBreakdown,
    CorrelationResult,
    Hypothesis,
    Insight,
    SegmentResult,
    SegmentValue,
    TrendPeriod,
    TrendResult,
)

# ------------------------------------------------------------------ #
# Section 6: hypothesis evaluation must be deterministic, never "the
# LLM declared it true."
# ------------------------------------------------------------------ #


def _trend(direction: str, overall_pct: float | None = 10.0) -> TrendResult:
    return TrendResult(
        metric_column="revenue", date_column="date", granularity="month", direction=direction,
        periods=[TrendPeriod(period="2026-01", value=100), TrendPeriod(period="2026-02", value=110, percentage_change=overall_pct)],
        overall_percentage_change=overall_pct,
    )


def test_evaluate_hypothesis_trend_supported_when_direction_matches() -> None:
    hyp = Hypothesis(statement="Revenue increased over time", evidence_required=["trend"], analytical_method="trend", column="revenue")
    status, evidence, reasoning = _evaluate_hypothesis(hyp, _trend("increasing"))
    assert status == "supported"
    assert "revenue" in evidence["metric_column"]


def test_evaluate_hypothesis_trend_rejected_when_direction_contradicts() -> None:
    hyp = Hypothesis(statement="Revenue increased over time", evidence_required=["trend"], analytical_method="trend", column="revenue")
    status, _, reasoning = _evaluate_hypothesis(hyp, _trend("decreasing", -8.0))
    assert status == "rejected"
    assert "actually decreasing" in reasoning


def test_evaluate_hypothesis_trend_inconclusive_when_flat() -> None:
    hyp = Hypothesis(statement="Revenue increased over time", evidence_required=["trend"], analytical_method="trend", column="revenue")
    status, _, _ = _evaluate_hypothesis(hyp, _trend("flat", 0.1))
    assert status == "inconclusive"


def test_evaluate_hypothesis_correlation_supported_for_strong_positive() -> None:
    hyp = Hypothesis(statement="Discount is positively correlated with revenue", evidence_required=["correlation"], analytical_method="correlation", column="discount", secondary_column="revenue")
    corr = CorrelationResult(column_a="discount", column_b="revenue", coefficient=0.85, strength="very_strong", direction="positive", sample_size=20)
    status, _, _ = _evaluate_hypothesis(hyp, corr)
    assert status == "supported"


def test_evaluate_hypothesis_correlation_rejected_for_negligible() -> None:
    hyp = Hypothesis(statement="X is correlated with Y", evidence_required=["correlation"], analytical_method="correlation", column="x", secondary_column="y")
    corr = CorrelationResult(column_a="x", column_b="y", coefficient=0.02, strength="negligible", direction="none", sample_size=20)
    status, _, _ = _evaluate_hypothesis(hyp, corr)
    assert status == "rejected"


def test_evaluate_hypothesis_segmentation_supported_for_disproportionate_share() -> None:
    hyp = Hypothesis(statement="North contributes disproportionately", evidence_required=["segmentation"], analytical_method="segmentation", column="region", secondary_column="revenue")
    seg = SegmentResult(
        dimension_column="region", metric_column="revenue", aggregation="sum",
        segments=[SegmentValue(segment="North", metric_value=800, row_count=5, contribution_percentage=80.0), SegmentValue(segment="South", metric_value=200, row_count=5, contribution_percentage=20.0)],
    )
    status, _, reasoning = _evaluate_hypothesis(hyp, seg)
    assert status == "supported"
    assert "North" in reasoning


def test_evaluate_hypothesis_segmentation_rejected_for_even_split() -> None:
    hyp = Hypothesis(statement="North contributes disproportionately", evidence_required=["segmentation"], analytical_method="segmentation", column="region", secondary_column="revenue")
    seg = SegmentResult(
        dimension_column="region", metric_column="revenue", aggregation="sum",
        segments=[SegmentValue(segment="North", metric_value=510, row_count=5, contribution_percentage=51.0), SegmentValue(segment="South", metric_value=490, row_count=5, contribution_percentage=49.0)],
    )
    status, _, _ = _evaluate_hypothesis(hyp, seg)
    assert status == "rejected"


def test_evaluate_hypothesis_anomaly_supported_when_anomalies_found() -> None:
    from app.schemas.deep_analysis import AnomalyResult

    hyp = Hypothesis(statement="Revenue has anomalies", evidence_required=["anomaly"], analytical_method="anomaly", column="revenue")
    anomalies = [AnomalyResult(metric_column="revenue", value=99999, expected_range="[0, 1000]", method="iqr", severity="high")]
    status, evidence, _ = _evaluate_hypothesis(hyp, anomalies)
    assert status == "supported"
    assert evidence["anomaly_count"] == 1


def test_evaluate_hypothesis_anomaly_rejected_when_none_found() -> None:
    hyp = Hypothesis(statement="Revenue has anomalies", evidence_required=["anomaly"], analytical_method="anomaly", column="revenue")
    status, evidence, _ = _evaluate_hypothesis(hyp, [])
    assert status == "rejected"
    assert evidence["anomaly_count"] == 0


# ------------------------------------------------------------------ #
# Section 13: confidence scoring is a weighted, explainable formula.
# ------------------------------------------------------------------ #


def test_score_confidence_is_bounded_and_explained() -> None:
    breakdown = _score_confidence("trend", _trend("increasing", 40.0).model_dump(), None)
    assert 0.0 <= breakdown.overall <= 1.0
    assert len(breakdown.reasons) == 4
    assert breakdown.overall == round(
        0.30 * breakdown.data_completeness + 0.20 * breakdown.sample_size_adequacy + 0.35 * breakdown.statistical_strength + 0.15 * breakdown.consistency,
        4,
    )


def test_score_confidence_higher_for_stronger_correlation() -> None:
    weak = _score_confidence("correlation", {"coefficient": 0.05, "sample_size": 20}, None)
    strong = _score_confidence("correlation", {"coefficient": 0.95, "sample_size": 20}, None)
    assert strong.overall > weak.overall


def test_score_confidence_higher_when_hypothesis_supported_vs_rejected() -> None:
    supported = _score_confidence("trend", {**_trend("increasing").model_dump(), "hypothesis_status": "supported"}, None)
    rejected = _score_confidence("trend", {**_trend("increasing").model_dump(), "hypothesis_status": "rejected"}, None)
    assert supported.consistency > rejected.consistency


def test_insight_priority_favors_higher_confidence_and_magnitude() -> None:
    high = Insight(
        title="High", finding="f", evidence={"overall_percentage_change": 80.0}, implication="i",
        confidence=ConfidenceBreakdown(data_completeness=1, sample_size_adequacy=1, statistical_strength=1, consistency=1, overall=0.9),
        source_step_type="trend",
    )
    low = Insight(
        title="Low", finding="f", evidence={"overall_percentage_change": 1.0}, implication="i",
        confidence=ConfidenceBreakdown(data_completeness=0.2, sample_size_adequacy=0.2, statistical_strength=0.2, consistency=0.2, overall=0.2),
        source_step_type="trend",
    )
    assert _insight_priority_score(high) > _insight_priority_score(low)


# ------------------------------------------------------------------ #
# Sections 15/16: contradiction + causal-language detection
# ------------------------------------------------------------------ #


def _insight(source_step_type, evidence, finding, implication="") -> Insight:
    return Insight(
        title="T", finding=finding, evidence=evidence, implication=implication or finding,
        confidence=ConfidenceBreakdown(data_completeness=1, sample_size_adequacy=1, statistical_strength=1, consistency=1, overall=0.8),
        source_step_type=source_step_type,
    )


def test_detect_contradictions_flags_increase_claim_on_decreasing_evidence() -> None:
    insight = _insight("trend", {"direction": "decreasing"}, "Revenue increased significantly.")
    problems = _detect_contradictions([insight])
    assert len(problems) == 1


def test_detect_contradictions_passes_consistent_claim() -> None:
    insight = _insight("trend", {"direction": "decreasing"}, "Revenue declined over the period.")
    assert _detect_contradictions([insight]) == []


def test_detect_contradictions_ignores_non_trend_insights() -> None:
    insight = _insight("segmentation", {"direction": "decreasing"}, "Revenue increased dramatically.")
    assert _detect_contradictions([insight]) == []


def test_detect_causal_language_flags_causal_wording_on_correlation() -> None:
    insight = _insight("correlation", {"coefficient": 0.8}, "The discount causes higher revenue.")
    violations = _detect_causal_language([insight])
    assert len(violations) == 1


def test_detect_causal_language_allows_association_wording() -> None:
    insight = _insight("correlation", {"coefficient": 0.8}, "The discount is associated with higher revenue.")
    assert _detect_causal_language([insight]) == []


def test_detect_causal_language_ignores_non_correlation_insights() -> None:
    # "drives" is legitimate phrasing for a segmentation-contribution
    # claim -- must not be flagged just because it appears in the word list.
    insight = _insight("segmentation", {"segments": []}, "North drives most of total revenue.")
    assert _detect_causal_language([insight]) == []


# ------------------------------------------------------------------ #
# Section 27: budget enforcement
# ------------------------------------------------------------------ #


def test_budget_flips_to_exceeded_past_the_configured_limit() -> None:
    settings = get_settings()
    budget = _init_budget(settings)
    assert budget.status == "ok"

    for _ in range(settings.max_deep_analysis_llm_calls + 1):
        budget = _consume(budget, settings, start_time=__import__("time").perf_counter(), llm_calls=1)

    assert budget.status == "exceeded"
    assert budget.exceeded_reason is not None


def test_budget_never_un_exceeds_once_tripped() -> None:
    settings = get_settings()
    budget = _init_budget(settings).model_copy(update={"status": "exceeded", "exceeded_reason": "test"})
    updated = _consume(budget, settings, start_time=__import__("time").perf_counter(), steps=1)
    assert updated.status == "exceeded"


# ------------------------------------------------------------------ #
# Task-key normalization (correlation column order shouldn't matter)
# ------------------------------------------------------------------ #


def test_task_key_normalizes_correlation_column_order() -> None:
    assert _task_key("correlation", "a", "b") == _task_key("correlation", "b", "a")


def test_task_key_none_for_missing_columns() -> None:
    assert _task_key("trend", None, None) is None
    assert _task_key("correlation", "a", None) is None
