"""
Node implementations for the Phase 10 deep-analysis graph.

Every node is either DETERMINISTIC (a plain Python function -- profiling,
data quality, the trend/anomaly/correlation/segmentation dispatcher,
evidence validation, confidence scoring, contradiction/causal-language
detection, chart building, limitations/report assembly, budget
accounting) or an LLM call scoped to exactly one narrow job (planning,
hypothesis generation, insight narrative, review, report narrative). The
rule from the Phase 10 spec is applied literally throughout: arithmetic,
percentages, and statistical judgments are never asked of a model --
every number a model is given is already computed, and every number a
model returns for something like `confidence` is immediately overridden
by a deterministically computed value (see _score_confidence / the
insight-construction code below), exactly like the existing codebase's
"deterministic override" pattern for sql_used/chart in agent/nodes.py.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from app.agent.deep_analysis.prompts import (
    DEEP_ANALYSIS_PLANNER_PROMPT,
    DEEP_REVIEWER_PROMPT,
    HYPOTHESIS_GENERATION_PROMPT,
    INSIGHT_WRITER_PROMPT,
    REPORT_WRITER_PROMPT,
)
from app.agent.deep_analysis.state import DeepAnalysisState
from app.core.config import Settings, get_settings
from app.schemas.deep_analysis import (
    AnalysisBudget,
    AnomalyResult,
    AxisSpec,
    ConfidenceBreakdown,
    CorrelationResult,
    DataQualityReport,
    DatasetOverview,
    DatasetProfile,
    DeepAnalysisPlan,
    DeepAnalysisResponse,
    DeepReviewResult,
    Hypothesis,
    HypothesisList,
    HypothesisResult,
    Insight,
    InsightNarrative,
    Report,
    ReportNarrative,
    SegmentResult,
    TraceStep,
    TrendResult,
    VisualizationSpec,
)
from app.services.data_quality_service import get_data_quality_service
from app.services.duckdb_service import get_duckdb_service
from app.services.exceptions import DatasetServiceError
from app.services.profiling_service import get_profiling_service
from app.services.statistics_service import InsufficientDataError, get_statistics_service
from app.utils.sql_utils import quote_identifier

logger = logging.getLogger(__name__)

_CAUSAL_PATTERN = re.compile(
    r"\bcauses?\b|\bcausing\b|\bcaused\b|\bleads?\s+to\b|\bled\s+to\b|\bdrives?\b|\bdriving\b|"
    r"\bresults?\s+in\b|\bresulted\s+in\b|\bbecause\s+of\b|\bdue\s+to\b",
    re.IGNORECASE,
)

_SCATTER_SAMPLE_LIMIT = 200


# ====================================================================== #
# Budget + trace helpers
# ====================================================================== #


def _init_budget(settings: Settings) -> AnalysisBudget:
    return AnalysisBudget(
        max_steps=settings.max_deep_analysis_steps,
        max_llm_calls=settings.max_deep_analysis_llm_calls,
        max_query_executions=settings.max_deep_analysis_query_executions,
        max_seconds=settings.max_deep_analysis_seconds,
    )


def _consume(budget: AnalysisBudget, settings: Settings, start_time: float, *, steps: int = 0, llm_calls: int = 0, queries: int = 0) -> AnalysisBudget:
    elapsed = round(time.perf_counter() - start_time, 3)
    updated = budget.model_copy(
        update={
            "steps_used": budget.steps_used + steps,
            "llm_calls_used": budget.llm_calls_used + llm_calls,
            "query_executions_used": budget.query_executions_used + queries,
            "elapsed_seconds": elapsed,
        }
    )
    if updated.status == "exceeded":
        return updated
    if updated.steps_used > updated.max_steps:
        return updated.model_copy(update={"status": "exceeded", "exceeded_reason": f"Reached the maximum of {updated.max_steps} analysis steps"})
    if updated.llm_calls_used > updated.max_llm_calls:
        return updated.model_copy(update={"status": "exceeded", "exceeded_reason": f"Reached the maximum of {updated.max_llm_calls} LLM calls"})
    if updated.query_executions_used > updated.max_query_executions:
        return updated.model_copy(update={"status": "exceeded", "exceeded_reason": f"Reached the maximum of {updated.max_query_executions} query executions"})
    if updated.elapsed_seconds > updated.max_seconds:
        return updated.model_copy(update={"status": "exceeded", "exceeded_reason": f"Reached the {updated.max_seconds}s time budget"})
    return updated


def _trace(step: str, status: str, duration_ms: float, summary: str) -> TraceStep:
    return TraceStep(step=step, status=status, duration_ms=round(max(duration_ms, 0.0), 3), result_summary=summary)


# ====================================================================== #
# 1. Dataset context + profile + quality (deterministic)
# ====================================================================== #


def dataset_context_node(state: DeepAnalysisState) -> dict:
    """First node: fixes the run's start time (everything else's
    budget.elapsed_seconds is measured from here) and fetches the schema
    -- the same DuckDBService.get_schema call every other graph in this
    project already uses (see agent/nodes.py::inspect_schema_node)."""

    settings = get_settings()
    run_start = time.perf_counter()
    schema = get_duckdb_service().get_schema(state["dataset_id"])
    duration_ms = (time.perf_counter() - run_start) * 1000

    return {
        "dataset_schema": schema,
        "start_time": run_start,
        "budget": _init_budget(settings),
        "trace": [_trace("dataset_context", "completed", duration_ms, f"{len(schema.columns)} column(s) found")],
        "hypotheses": [],
        "hypothesis_results": [],
        "trends": [],
        "anomalies": [],
        "correlations": [],
        "segments": [],
        "insights": [],
        "recommendations": [],
        "visualizations": [],
        "review": None,
        "review_retry_count": 0,
    }


def data_profile_node(state: DeepAnalysisState) -> dict:
    """Section 1: autonomous profiling. A profiling failure (e.g. the
    dataset was deleted mid-run) is not recoverable -- nothing downstream
    can meaningfully run without a profile -- so this re-raises rather
    than degrading, unlike data_quality_node below."""

    started = time.perf_counter()
    profile = get_profiling_service().profile_dataset(state["dataset_id"])
    duration_ms = (time.perf_counter() - started) * 1000

    budget = _consume(state["budget"], get_settings(), state["start_time"], queries=2)
    trace = [*state.get("trace", []), _trace("data_profile", "completed", duration_ms, f"{profile.row_count} row(s), {profile.column_count} column(s)")]
    return {"profile": profile, "budget": budget, "trace": trace}


def data_quality_node(state: DeepAnalysisState) -> dict:
    """Section 2: data quality checks. Failure here degrades gracefully
    (section 32) -- an empty DataQualityReport, not a crash -- since the
    rest of the pipeline does not depend on it."""

    started = time.perf_counter()
    profile = state["profile"]
    try:
        quality = get_data_quality_service().assess(state["dataset_id"], profile)
        status, summary = "completed", f"{len(quality.issues)} issue(s) found"
    except Exception:  # noqa: BLE001 -- see docstring
        logger.exception("Data quality assessment failed for dataset %s", state["dataset_id"])
        quality = DataQualityReport(issues=[], duplicate_row_count=0, total_row_count=profile.row_count if profile else 0)
        status, summary = "failed", "Data quality assessment failed; continuing without it."

    duration_ms = (time.perf_counter() - started) * 1000
    budget = _consume(state["budget"], get_settings(), state["start_time"], queries=3)
    trace = [*state.get("trace", []), _trace("data_quality", status, duration_ms, summary)]
    return {"data_quality": quality, "budget": budget, "trace": trace}


# ====================================================================== #
# Compact prompt context builders (section 41: never dump raw rows)
# ====================================================================== #


def _profile_summary_for_prompt(profile: DatasetProfile | None) -> str:
    if profile is None:
        return "(no profile available)"

    lines = [
        f"Rows: {profile.row_count}, Columns: {profile.column_count}",
        f"Numeric columns: {profile.numeric_columns}",
        f"Categorical columns: {profile.categorical_columns}",
        f"Date columns: {profile.date_columns}",
    ]
    if profile.identifier_columns:
        lines.append(f"Likely identifier columns (do not use as analysis targets): {profile.identifier_columns}")
    if profile.high_cardinality_columns:
        lines.append(f"High-cardinality columns (avoid segmenting by these): {profile.high_cardinality_columns}")

    for column in profile.columns:
        if column.kind == "numeric":
            lines.append(
                f"  - {column.name} (numeric): min={column.min_value}, max={column.max_value}, "
                f"mean={column.mean}, missing={column.missing_percentage}%"
            )
        elif column.kind == "date":
            lines.append(f"  - {column.name} (date): {column.min_date} to {column.max_date}")
        elif column.kind == "categorical":
            lines.append(f"  - {column.name} (categorical): {column.distinct_count} distinct value(s), missing={column.missing_percentage}%")
    return "\n".join(lines)


def _step_supported(step_type: str, column: str | None, secondary_column: str | None, profile: DatasetProfile | None) -> bool:
    """Section 4/28: dataset-aware tool selection, enforced deterministically
    -- never trusted from the model's own judgment about what's supported."""

    if profile is None or not column:
        return False
    numeric, categorical, dates = set(profile.numeric_columns), set(profile.categorical_columns), set(profile.date_columns)

    if step_type == "trend":
        return bool(dates) and column in numeric
    if step_type == "anomaly":
        return column in numeric
    if step_type == "correlation":
        return bool(secondary_column) and column in numeric and secondary_column in numeric and column != secondary_column
    if step_type == "segmentation":
        return bool(secondary_column) and column in categorical and secondary_column in numeric
    return False


# ====================================================================== #
# 3/4. Planner
# ====================================================================== #


def build_planner_node(structured_model: Runnable) -> Callable[[DeepAnalysisState], dict]:
    def planner_node(state: DeepAnalysisState) -> dict:
        settings = get_settings()
        profile = state["profile"]

        content = _profile_summary_for_prompt(profile)
        if state.get("user_question"):
            content += f"\n\nUser question: {state['user_question']}"
        else:
            content += "\n\nNo specific question was asked -- autonomously investigate this dataset and surface what's most worth knowing."

        messages = [SystemMessage(content=DEEP_ANALYSIS_PLANNER_PROMPT), HumanMessage(content=content)]
        plan: DeepAnalysisPlan = structured_model.invoke(messages)

        valid_steps = [s for s in plan.steps if _step_supported(s.step_type, s.column, s.secondary_column, profile)]
        plan = plan.model_copy(update={"steps": valid_steps[: settings.max_deep_analysis_steps]})

        budget = _consume(state["budget"], settings, state["start_time"], llm_calls=1)
        trace = [*state.get("trace", []), _trace("planner", "completed", 0.0, f"{len(plan.steps)} step(s) selected -- {plan.objective}")]
        return {"analysis_plan": plan, "budget": budget, "trace": trace}

    return planner_node


# ====================================================================== #
# 5. Hypothesis generation
# ====================================================================== #


def build_hypothesis_generator_node(structured_model: Runnable) -> Callable[[DeepAnalysisState], dict]:
    def hypothesis_generator_node(state: DeepAnalysisState) -> dict:
        settings = get_settings()
        profile = state["profile"]
        plan = state.get("analysis_plan")

        content_parts = [
            _profile_summary_for_prompt(profile),
            f"Analysis plan objective: {plan.objective if plan else '(none)'}",
            f"Planned steps: {[s.model_dump() for s in plan.steps] if plan else []}",
        ]
        if state.get("user_question"):
            content_parts.append(f"User question: {state['user_question']}")

        messages = [SystemMessage(content=HYPOTHESIS_GENERATION_PROMPT), HumanMessage(content="\n\n".join(content_parts))]
        result: HypothesisList = structured_model.invoke(messages)

        hypotheses = [h for h in result.hypotheses if _step_supported(h.analytical_method, h.column, h.secondary_column, profile)]
        hypotheses = hypotheses[: settings.max_hypotheses]

        budget = _consume(state["budget"], settings, state["start_time"], llm_calls=1)
        trace = [*state.get("trace", []), _trace("hypothesis_generator", "completed", 0.0, f"{len(hypotheses)} hypothesis(es) generated")]
        return {"hypotheses": hypotheses, "budget": budget, "trace": trace}

    return hypothesis_generator_node


# ====================================================================== #
# 6/7/8/9/10. Analysis executor: deterministic dispatcher over
# trend/anomaly/correlation/segmentation, and hypothesis testing.
# ====================================================================== #


def _task_key(step_type: str, column: str | None, secondary_column: str | None) -> tuple | None:
    if not column:
        return None
    if step_type == "trend":
        return ("trend", column, None)
    if step_type == "anomaly":
        return ("anomaly", column, None)
    if step_type == "correlation":
        if not secondary_column:
            return None
        a, b = sorted([column, secondary_column])
        return ("correlation", a, b)
    if step_type == "segmentation":
        if not secondary_column:
            return None
        return ("segmentation", column, secondary_column)
    return None


def _choose_granularity(profile: DatasetProfile, date_column: str) -> str:
    column = next((c for c in profile.columns if c.name == date_column), None)
    if not column or not column.min_date or not column.max_date:
        return "month"
    try:
        span = (date_cls.fromisoformat(column.max_date[:10]) - date_cls.fromisoformat(column.min_date[:10])).days
    except ValueError:
        return "month"
    if span <= 31:
        return "day"
    if span <= 180:
        return "week"
    return "month"


def _summarize_result(step_type: str, result) -> str:
    if step_type == "trend":
        return f"{result.direction} trend, overall change {result.overall_percentage_change}%"
    if step_type == "anomaly":
        return f"{len(result)} anomaly(ies) found"
    if step_type == "correlation":
        return f"{result.strength} {result.direction} correlation (r={result.coefficient})"
    if step_type == "segmentation":
        top = result.segments[0] if result.segments else None
        return f"top segment '{top.segment}' = {top.contribution_percentage}%" if top else "no segments"
    return "done"


def analysis_executor_node(state: DeepAnalysisState) -> dict:
    settings = get_settings()
    dataset_id = state["dataset_id"]
    profile = state["profile"]
    stats = get_statistics_service()

    budget = state["budget"]
    trace = list(state.get("trace", []))
    date_column = profile.date_columns[0] if profile and profile.date_columns else None
    granularity = _choose_granularity(profile, date_column) if date_column else "month"

    tasks: dict[tuple, str] = {}
    plan = state.get("analysis_plan")
    if plan:
        for step in plan.steps:
            key = _task_key(step.step_type, step.column, step.secondary_column)
            if key:
                tasks.setdefault(key, step.reason)
    for hyp in state.get("hypotheses", []):
        key = _task_key(hyp.analytical_method, hyp.column, hyp.secondary_column)
        if key:
            tasks.setdefault(key, hyp.statement)

    trends: list[TrendResult] = []
    anomalies: list[AnomalyResult] = []
    correlations: list[CorrelationResult] = []
    segments: list[SegmentResult] = []
    results_by_key: dict[tuple, object] = {}
    errors_by_key: dict[tuple, str] = {}

    for key in tasks:
        if budget.status == "exceeded":
            break
        step_type, col, col2 = key
        started = time.perf_counter()
        try:
            if step_type == "trend":
                if not date_column:
                    raise InsufficientDataError("No date column available for trend analysis")
                result = stats.detect_trend(dataset_id, date_column, col, granularity=granularity)
                trends.append(result)
                budget = _consume(budget, settings, state["start_time"], queries=1)
            elif step_type == "anomaly":
                result = stats.detect_anomalies(dataset_id, col, method="iqr", limit=5)
                anomalies.extend(result)
                budget = _consume(budget, settings, state["start_time"], queries=2)
            elif step_type == "correlation":
                result = stats.calculate_correlation(dataset_id, col, col2)
                correlations.append(result)
                budget = _consume(budget, settings, state["start_time"], queries=1)
            elif step_type == "segmentation":
                result = stats.segment_data(dataset_id, col, col2, aggregation="sum", date_column=date_column, limit=10)
                segments.append(result)
                budget = _consume(budget, settings, state["start_time"], queries=2 if date_column else 1)
            else:  # pragma: no cover -- _task_key never emits an unknown step_type
                raise ValueError(f"Unknown step type '{step_type}'")
            results_by_key[key] = result
            trace.append(_trace(f"{step_type}:{col}" + (f"/{col2}" if col2 else ""), "completed", (time.perf_counter() - started) * 1000, _summarize_result(step_type, result)))
        except (InsufficientDataError, DatasetServiceError, ValueError) as exc:
            errors_by_key[key] = str(exc)
            trace.append(_trace(f"{step_type}:{col}" + (f"/{col2}" if col2 else ""), "skipped", (time.perf_counter() - started) * 1000, str(exc)))
        budget = _consume(budget, settings, state["start_time"], steps=1)

    hypothesis_results: list[HypothesisResult] = []
    for hyp in state.get("hypotheses", []):
        key = _task_key(hyp.analytical_method, hyp.column, hyp.secondary_column)
        if key is not None and key in results_by_key:
            status, evidence, reasoning = _evaluate_hypothesis(hyp, results_by_key[key])
        else:
            status = "inconclusive"
            evidence = {}
            reasoning = (errors_by_key.get(key, "Could not be tested within this run's budget.") if key else "References columns that could not be matched to a valid analysis.")
        hypothesis_results.append(HypothesisResult(hypothesis=hyp, status=status, evidence=evidence, reasoning=reasoning))

    return {
        "trends": trends,
        "anomalies": anomalies,
        "correlations": correlations,
        "segments": segments,
        "hypothesis_results": hypothesis_results,
        "budget": budget,
        "trace": trace,
    }


def _evaluate_hypothesis(hypothesis: Hypothesis, result) -> tuple[str, dict, str]:
    """Section 6: DETERMINISTICALLY assign supported/rejected/inconclusive
    from the real computed result -- an LLM is never allowed to simply
    declare a hypothesis true."""

    statement = hypothesis.statement.lower()
    method = hypothesis.analytical_method

    if method == "trend":
        trend: TrendResult = result
        evidence = trend.model_dump()
        expects_increase = any(k in statement for k in ("increas", "grow", "rising", "higher", "up"))
        expects_decrease = any(k in statement for k in ("decreas", "declin", "falling", "lower", "drop"))
        if trend.direction == "increasing" and expects_increase:
            return "supported", evidence, f"'{trend.metric_column}' is increasing (overall change {trend.overall_percentage_change}%), matching the hypothesis."
        if trend.direction == "decreasing" and expects_decrease:
            return "supported", evidence, f"'{trend.metric_column}' is decreasing (overall change {trend.overall_percentage_change}%), matching the hypothesis."
        if trend.direction == "increasing" and expects_decrease:
            return "rejected", evidence, f"'{trend.metric_column}' is actually increasing, contradicting the hypothesis."
        if trend.direction == "decreasing" and expects_increase:
            return "rejected", evidence, f"'{trend.metric_column}' is actually decreasing, contradicting the hypothesis."
        return "inconclusive", evidence, f"'{trend.metric_column}' is {trend.direction}, which does not clearly confirm or refute this hypothesis."

    if method == "anomaly":
        anomalies: list[AnomalyResult] = result
        evidence = {
            "metric_column": hypothesis.column,
            "anomaly_count": len(anomalies),
            "anomalies": [a.model_dump() for a in anomalies[:5]],
        }
        if anomalies:
            return "supported", evidence, f"{len(anomalies)} anomalous value(s) were detected in '{hypothesis.column}'."
        return "rejected", evidence, f"No anomalies were detected in '{hypothesis.column}' using the IQR method."

    if method == "correlation":
        corr: CorrelationResult = result
        evidence = corr.model_dump()
        expects_positive = "positive" in statement or "negative" not in statement and "correlat" in statement and "inverse" not in statement
        expects_negative = "negative" in statement or "inverse" in statement
        if corr.strength == "negligible":
            return "rejected", evidence, f"The correlation coefficient ({corr.coefficient}) is negligible."
        if expects_negative and corr.direction != "negative":
            return "rejected", evidence, f"The correlation is {corr.direction} ({corr.coefficient}), not negative as hypothesized."
        if expects_positive and not expects_negative and corr.direction != "positive":
            return "rejected", evidence, f"The correlation is {corr.direction} ({corr.coefficient}), not positive as hypothesized."
        if corr.strength in ("moderate", "strong", "very_strong"):
            return "supported", evidence, f"A {corr.strength} {corr.direction} correlation (r={corr.coefficient}) was found."
        return "inconclusive", evidence, f"Only a weak correlation (r={corr.coefficient}) was found -- not strong enough to confidently support or reject this."

    if method == "segmentation":
        seg: SegmentResult = result
        evidence = seg.model_dump()
        if not seg.segments:
            return "inconclusive", evidence, "No segments were found."
        top = seg.segments[0]
        even_share = 100.0 / len(seg.segments)
        if top.contribution_percentage >= even_share * 1.5:
            return "supported", evidence, f"'{top.segment}' contributes {top.contribution_percentage}% of {seg.metric_column}, well above an even {even_share:.1f}% share across {len(seg.segments)} segment(s)."
        if top.contribution_percentage <= even_share * 1.1:
            return "rejected", evidence, f"The top segment ('{top.segment}') contributes only {top.contribution_percentage}%, close to an even {even_share:.1f}% share -- no segment dominates."
        return "inconclusive", evidence, f"'{top.segment}' contributes {top.contribution_percentage}%, somewhat above an even share but not decisively disproportionate."

    return "inconclusive", {}, "This hypothesis's analytical method is not supported."


def route_after_executor(state: DeepAnalysisState) -> str:
    return "report_generator" if state["budget"].status == "exceeded" else "insight_engine"


# ====================================================================== #
# 11-14. Insight engine
# ====================================================================== #


@dataclass
class _EvidenceCandidate:
    source_step_type: str
    evidence: dict
    hint: str | None  # the hypothesis statement this evidence came from, if any


def _collect_evidence_candidates(state: DeepAnalysisState) -> list[_EvidenceCandidate]:
    """Every hypothesis result worth reporting (supported OR rejected --
    a rejected hypothesis is itself a real, evidence-backed finding, e.g.
    'contrary to expectation, revenue actually declined') plus every
    other computed result NOT already covered by a hypothesis, so
    insight discovery surfaces more than just what was explicitly
    hypothesized."""

    candidates: list[_EvidenceCandidate] = []
    covered: set[tuple] = set()

    for hr in state.get("hypothesis_results", []):
        if hr.status == "inconclusive":
            continue
        key = _task_key(hr.hypothesis.analytical_method, hr.hypothesis.column, hr.hypothesis.secondary_column)
        if key:
            covered.add(key)
        evidence = {**hr.evidence, "hypothesis_status": hr.status, "hypothesis_statement": hr.hypothesis.statement}
        candidates.append(_EvidenceCandidate(hr.hypothesis.analytical_method, evidence, hr.hypothesis.statement))

    for trend in state.get("trends", []):
        if ("trend", trend.metric_column, None) in covered:
            continue
        candidates.append(_EvidenceCandidate("trend", trend.model_dump(), None))

    anomalies_by_column: dict[str, list[AnomalyResult]] = {}
    for anomaly in state.get("anomalies", []):
        anomalies_by_column.setdefault(anomaly.metric_column, []).append(anomaly)
    for column, items in anomalies_by_column.items():
        if ("anomaly", column, None) in covered:
            continue
        candidates.append(
            _EvidenceCandidate("anomaly", {"metric_column": column, "anomaly_count": len(items), "anomalies": [a.model_dump() for a in items]}, None)
        )

    for corr in state.get("correlations", []):
        key = ("correlation", *sorted([corr.column_a, corr.column_b]))
        if key in covered:
            continue
        candidates.append(_EvidenceCandidate("correlation", corr.model_dump(), None))

    for seg in state.get("segments", []):
        if ("segmentation", seg.dimension_column, seg.metric_column) in covered:
            continue
        candidates.append(_EvidenceCandidate("segmentation", seg.model_dump(), None))

    return candidates


def _lookup_completeness(evidence: dict, profile: DatasetProfile | None) -> float:
    if profile is None:
        return 0.8
    names = [evidence.get(k) for k in ("metric_column", "date_column", "column_a", "column_b", "dimension_column") if evidence.get(k)]
    by_name = {c.name: c for c in profile.columns}
    ratios = [1.0 - (by_name[n].missing_percentage / 100.0) for n in names if n in by_name]
    return sum(ratios) / len(ratios) if ratios else 0.8


def _lookup_sample_adequacy(source_step_type: str, evidence: dict, profile: DatasetProfile | None) -> float:
    if source_step_type == "correlation":
        return min(1.0, (evidence.get("sample_size") or 0) / 30.0)
    if source_step_type == "trend":
        return min(1.0, len(evidence.get("periods") or []) / 6.0)
    if source_step_type == "segmentation":
        return min(1.0, len(evidence.get("segments") or []) / 4.0)
    row_count = profile.row_count if profile else 0
    return min(1.0, row_count / 30.0)


def _lookup_statistical_strength(source_step_type: str, evidence: dict) -> float:
    if source_step_type == "trend":
        direction = evidence.get("direction")
        pct = abs(evidence.get("overall_percentage_change") or 0)
        if direction in ("increasing", "decreasing"):
            return min(1.0, pct / 50.0)
        return 0.4 if direction == "volatile" else 0.25
    if source_step_type == "anomaly":
        count = evidence.get("anomaly_count") or 0
        return min(1.0, count / 5.0) if count else 0.1
    if source_step_type == "correlation":
        return abs(evidence.get("coefficient") or 0.0)
    if source_step_type == "segmentation":
        segments = evidence.get("segments") or []
        if not segments:
            return 0.1
        top_share = segments[0].get("contribution_percentage", 0.0)
        even_share = 100.0 / len(segments)
        return min(1.0, max(0.0, (top_share / even_share - 1.0))) if even_share else 0.5
    return 0.5


def _score_confidence(source_step_type: str, evidence: dict, profile: DatasetProfile | None) -> ConfidenceBreakdown:
    """Section 13: an explainable, weighted combination -- never a bare
    number the model invents."""

    data_completeness = round(_lookup_completeness(evidence, profile), 4)
    sample_size_adequacy = round(_lookup_sample_adequacy(source_step_type, evidence, profile), 4)
    statistical_strength = round(_lookup_statistical_strength(source_step_type, evidence), 4)
    hypothesis_status = evidence.get("hypothesis_status")
    consistency = 1.0 if hypothesis_status == "supported" else (0.6 if hypothesis_status == "rejected" else 0.75)

    overall = 0.30 * data_completeness + 0.20 * sample_size_adequacy + 0.35 * statistical_strength + 0.15 * consistency
    overall = round(min(max(overall, 0.0), 1.0), 4)

    reasons = [
        f"Data completeness: {data_completeness:.0%} (based on the missing-value rate of the column(s) involved)",
        f"Sample size adequacy: {sample_size_adequacy:.0%}",
        f"Statistical strength: {statistical_strength:.0%} (magnitude/strength of the {source_step_type} result)",
        f"Consistency: {consistency:.0%}" + (" (matched a tested hypothesis)" if hypothesis_status else ""),
    ]
    return ConfidenceBreakdown(
        data_completeness=data_completeness,
        sample_size_adequacy=sample_size_adequacy,
        statistical_strength=statistical_strength,
        consistency=consistency,
        overall=overall,
        reasons=reasons,
    )


def _magnitude_proxy(evidence: dict) -> float:
    if evidence.get("overall_percentage_change") is not None:
        return min(2.0, abs(evidence["overall_percentage_change"]) / 50.0)
    if "coefficient" in evidence:
        return abs(evidence.get("coefficient") or 0.0)
    segments = evidence.get("segments")
    if segments:
        return (segments[0].get("contribution_percentage", 0.0)) / 100.0
    if "anomaly_count" in evidence:
        return min(1.0, (evidence.get("anomaly_count") or 0) / 5.0)
    return 0.5


def _insight_priority_score(insight: Insight) -> float:
    """Section 14: rank by a combination of confidence and magnitude --
    never returned in the order the model happened to produce them."""

    return insight.confidence.overall * (1.0 + _magnitude_proxy(insight.evidence))


def _build_insight(structured_model: Runnable, candidate: _EvidenceCandidate, confidence: ConfidenceBreakdown, corrections_hint: str | None) -> Insight | None:
    content_parts = [
        f"Evidence type: {candidate.source_step_type}",
        f"Evidence (the ONLY numbers you may reference): {json.dumps(candidate.evidence, default=str)}",
        f"Fixed confidence score (already computed, for your context only -- do not restate a different number): {confidence.overall:.2f}",
    ]
    if candidate.hint:
        content_parts.append(f"This evidence was gathered to test the hypothesis: {candidate.hint}")
    if corrections_hint:
        content_parts.append(f"A previous attempt at this insight was rejected by the Reviewer for: {corrections_hint}. Fix this.")

    messages = [SystemMessage(content=INSIGHT_WRITER_PROMPT), HumanMessage(content="\n\n".join(content_parts))]
    try:
        narrative: InsightNarrative = structured_model.invoke(messages)
    except Exception:  # noqa: BLE001 -- one insight failing must not fail the whole run (section 32)
        logger.exception("Insight narrative generation failed for %s evidence", candidate.source_step_type)
        return None

    return Insight(
        title=narrative.title,
        finding=narrative.finding,
        evidence=candidate.evidence,
        metric=narrative.metric,
        comparison=narrative.comparison,
        significance=narrative.significance,
        confidence=confidence,
        implication=narrative.implication,
        source_step_type=candidate.source_step_type,  # type: ignore[arg-type]
    )


def build_insight_engine_node(structured_model: Runnable) -> Callable[[DeepAnalysisState], dict]:
    def insight_engine_node(state: DeepAnalysisState) -> dict:
        settings = get_settings()
        profile = state.get("profile")
        budget = state["budget"]
        trace = list(state.get("trace", []))

        review = state.get("review")
        corrections_hint = "; ".join(review.corrections) if review is not None and not review.approved else None

        candidates = _collect_evidence_candidates(state)
        candidates.sort(key=lambda c: _magnitude_proxy(c.evidence), reverse=True)

        insights: list[Insight] = []
        for candidate in candidates[: settings.max_insights * 2]:
            if budget.status == "exceeded" or budget.llm_calls_used >= settings.max_deep_analysis_llm_calls:
                break
            started = time.perf_counter()
            confidence = _score_confidence(candidate.source_step_type, candidate.evidence, profile)
            insight = _build_insight(structured_model, candidate, confidence, corrections_hint)
            budget = _consume(budget, settings, state["start_time"], llm_calls=1)
            duration_ms = (time.perf_counter() - started) * 1000
            if insight is None:
                trace.append(_trace(f"insight:{candidate.source_step_type}", "failed", duration_ms, "Insight generation failed for this evidence item."))
                continue
            insights.append(insight)
            trace.append(_trace(f"insight:{candidate.source_step_type}", "completed", duration_ms, insight.title))

        insights.sort(key=_insight_priority_score, reverse=True)
        insights = insights[: settings.max_insights]

        return {"insights": insights, "budget": budget, "trace": trace}

    return insight_engine_node


# ====================================================================== #
# Evidence validator (section 12/15 -- structural check, deterministic)
# ====================================================================== #


def evidence_validator_node(state: DeepAnalysisState) -> dict:
    """Drops any insight with no evidence attached (structurally should
    never happen, given _build_insight's design -- this is defense in
    depth) and recomputes confidence from the evidence to catch any
    drift, rather than trusting whatever was set earlier."""

    profile = state.get("profile")
    trace = list(state.get("trace", []))
    valid: list[Insight] = []

    for insight in state.get("insights", []):
        started = time.perf_counter()
        if not insight.evidence:
            trace.append(_trace(f"evidence_validator:{insight.title}", "failed", 0.0, "Dropped: no evidence attached."))
            continue
        recomputed = _score_confidence(insight.source_step_type, insight.evidence, profile)
        if abs(recomputed.overall - insight.confidence.overall) > 0.01:
            insight = insight.model_copy(update={"confidence": recomputed})
        valid.append(insight)
        trace.append(_trace(f"evidence_validator:{insight.title}", "completed", (time.perf_counter() - started) * 1000, "Evidence present; confidence verified."))

    return {"insights": valid, "trace": trace}


# ====================================================================== #
# 15/16. Reviewer (contradiction + causal-language detection)
# ====================================================================== #


def _detect_contradictions(insights: list[Insight]) -> list[str]:
    problems = []
    for insight in insights:
        if insight.source_step_type != "trend":
            continue
        direction = insight.evidence.get("direction")
        text = f"{insight.finding} {insight.implication}".lower()
        says_increase = any(k in text for k in ("increas", "grew", "growth", "rising", "went up", "higher"))
        says_decrease = any(k in text for k in ("decreas", "declin", "fell", "dropped", "went down", "lower"))
        if direction == "decreasing" and says_increase and not says_decrease:
            problems.append(f"'{insight.title}' claims an increase, but the evidence direction is 'decreasing'.")
        if direction == "increasing" and says_decrease and not says_increase:
            problems.append(f"'{insight.title}' claims a decrease, but the evidence direction is 'increasing'.")
    return problems


def _detect_causal_language(insights: list[Insight]) -> list[str]:
    violations = []
    for insight in insights:
        if insight.source_step_type != "correlation":
            continue
        text = f"{insight.finding} {insight.implication}"
        if _CAUSAL_PATTERN.search(text):
            violations.append(f"'{insight.title}' uses causal language for a correlation-based finding -- correlation is not causation.")
    return violations


def _format_insights_for_review(insights: list[Insight]) -> str:
    if not insights:
        return "(no insights were generated)"
    blocks = [
        f"Title: {i.title}\nFinding: {i.finding}\nImplication: {i.implication}\nSource type: {i.source_step_type}\nEvidence: {json.dumps(i.evidence, default=str)}"
        for i in insights
    ]
    return "\n\n".join(blocks)


def build_deep_reviewer_node(structured_model: Runnable) -> Callable[[DeepAnalysisState], dict]:
    def deep_reviewer_node(state: DeepAnalysisState) -> dict:
        settings = get_settings()
        insights = state.get("insights", [])
        budget = state["budget"]

        contradictions = _detect_contradictions(insights)
        causal_violations = _detect_causal_language(insights)

        # Nothing to review (no insights survived, e.g. the budget ran out
        # before any were produced) or no budget left for another LLM
        # call: skip the call entirely rather than spending budget on a
        # review with nothing to check -- approve trivially (there is
        # nothing to reject) so routing proceeds straight to the report.
        if not insights or budget.status == "exceeded" or budget.llm_calls_used >= settings.max_deep_analysis_llm_calls:
            review = DeepReviewResult(approved=True, confidence=0.0, issues=[] if insights else ["No insights were available to review."])
            retry_count = state.get("review_retry_count", 0)
            trace = [*state.get("trace", []), _trace("reviewer", "skipped", 0.0, "Skipped: no insights to review or no budget remaining.")]
            return {"review": review, "review_retry_count": retry_count, "budget": budget, "trace": trace}

        content = (
            f"{_format_insights_for_review(insights)}\n\n"
            f"Deterministically detected contradictions: {contradictions or 'none'}\n"
            f"Deterministically detected causal-language violations: {causal_violations or 'none'}"
        )
        messages = [SystemMessage(content=DEEP_REVIEWER_PROMPT), HumanMessage(content=content)]
        review: DeepReviewResult = structured_model.invoke(messages)

        # Deterministic override, same pattern as every other reviewer in
        # this codebase (agent/reviewer.py): a real, already-detected
        # contradiction or causal-language violation forces rejection
        # regardless of what the model itself concluded.
        if (contradictions or causal_violations) and review.approved:
            review = review.model_copy(update={"approved": False})
        review = review.model_copy(
            update={
                "contradictions": list(dict.fromkeys([*review.contradictions, *contradictions])),
                "causal_language_violations": list(dict.fromkeys([*review.causal_language_violations, *causal_violations])),
            }
        )

        retry_count = state.get("review_retry_count", 0)
        if not review.approved:
            retry_count += 1

        budget = _consume(budget, settings, state["start_time"], llm_calls=1)
        trace = [*state.get("trace", []), _trace("reviewer", "completed", 0.0, f"approved={review.approved}, retry_cycle={retry_count}")]
        return {"review": review, "review_retry_count": retry_count, "budget": budget, "trace": trace}

    return deep_reviewer_node


def route_after_deep_reviewer(state: DeepAnalysisState) -> str:
    settings = get_settings()
    review = state["review"]
    if not review.approved and state.get("review_retry_count", 0) < settings.max_deep_analysis_review_retries and state["budget"].status != "exceeded":
        return "insight_engine"
    return "report_generator"


# ====================================================================== #
# 20-22. Report generation
# ====================================================================== #


def _fallback_summary(state: DeepAnalysisState) -> str:
    count = len(state.get("insights", []))
    if count:
        return f"Analysis produced {count} reviewed insight(s); see key findings below."
    return "The analysis could not produce a confident summary from the available data."


def _format_report_context(state: DeepAnalysisState) -> str:
    parts = [f"User question: {state.get('user_question') or '(none -- general investigation)'}"]
    parts.append(f"Approved insights:\n{json.dumps([i.model_dump() for i in state.get('insights', [])], default=str)}")
    quality = state.get("data_quality")
    if quality is not None:
        parts.append(f"Data quality issues:\n{json.dumps([i.model_dump() for i in quality.issues], default=str)}")
    review = state.get("review")
    if review is not None:
        parts.append(f"Reviewer confidence: {review.confidence}")
    return "\n\n".join(parts)


def _build_limitations(profile: DatasetProfile | None, quality: DataQualityReport, correlations: list[CorrelationResult], anomalies: list[AnomalyResult], budget: AnalysisBudget) -> list[str]:
    limitations = []
    missing_columns = [i for i in quality.issues if i.issue_type == "missing_values"]
    if missing_columns:
        limitations.append(f"{len(missing_columns)} column(s) contain missing values -- see Data Quality for details.")
    for issue in quality.issues:
        if issue.issue_type == "narrow_time_range":
            limitations.append(issue.description)
    if correlations:
        limitations.append("Correlation does not establish causation; no causal relationship should be inferred from the correlations reported here.")
    if anomalies:
        methods = sorted({a.method for a in anomalies})
        limitations.append(f"Anomaly detection used the {'/'.join(methods)} method; other legitimate outliers may exist that this method does not catch.")
    limitations.append("No external market or contextual data was available; conclusions are based solely on the provided dataset.")
    if budget.status == "exceeded":
        limitations.append(f"This analysis stopped early after reaching its computation budget ({budget.exceeded_reason}); some planned analyses may not have run.")
    return limitations


def build_report_generator_node(structured_model: Runnable) -> Callable[[DeepAnalysisState], dict]:
    def report_generator_node(state: DeepAnalysisState) -> dict:
        settings = get_settings()
        profile = state.get("profile")
        quality = state.get("data_quality") or DataQualityReport(total_row_count=profile.row_count if profile else 0)
        insights = state.get("insights", [])
        budget = state["budget"]

        try:
            if budget.status != "exceeded" and budget.llm_calls_used < settings.max_deep_analysis_llm_calls:
                messages = [SystemMessage(content=REPORT_WRITER_PROMPT), HumanMessage(content=_format_report_context(state))]
                narrative: ReportNarrative = structured_model.invoke(messages)
                budget = _consume(budget, settings, state["start_time"], llm_calls=1)
            else:
                narrative = ReportNarrative(executive_summary=_fallback_summary(state), recommendations=[])
        except Exception:  # noqa: BLE001 -- section 32: a failed report narrative must not fail the whole run
            logger.exception("Report narrative generation failed for dataset %s", state["dataset_id"])
            narrative = ReportNarrative(executive_summary=_fallback_summary(state), recommendations=[])

        insight_titles = {i.title for i in insights}
        recommendations = [
            r if r.grounded_insight_title in insight_titles else r.model_copy(update={"grounded_insight_title": None})
            for r in narrative.recommendations
        ]

        limitations = _build_limitations(profile, quality, state.get("correlations", []), state.get("anomalies", []), budget)
        overview = DatasetOverview(
            row_count=profile.row_count if profile else 0,
            column_count=profile.column_count if profile else 0,
            numeric_column_count=len(profile.numeric_columns) if profile else 0,
            categorical_column_count=len(profile.categorical_columns) if profile else 0,
            date_column_count=len(profile.date_columns) if profile else 0,
        )

        report = Report(
            executive_summary=narrative.executive_summary,
            dataset_overview=overview,
            data_quality=quality.issues,
            key_findings=insights,
            trends=state.get("trends", []),
            anomalies=state.get("anomalies", []),
            segment_analysis=state.get("segments", []),
            correlations=state.get("correlations", []),
            recommendations=recommendations,
            limitations=limitations,
        )

        trace = [*state.get("trace", []), _trace("report_generator", "completed", 0.0, f"{len(insights)} insight(s), {len(recommendations)} recommendation(s)")]
        return {"report": report, "recommendations": recommendations, "budget": budget, "trace": trace}

    return report_generator_node


# ====================================================================== #
# 23/24. Chart recommendation (deterministic)
# ====================================================================== #


def _fetch_scatter_sample(dataset_id: str, column_a: str, column_b: str, settings: Settings) -> list[dict] | None:
    quoted_a, quoted_b = quote_identifier(column_a), quote_identifier(column_b)
    sql = f"SELECT {quoted_a} AS x, {quoted_b} AS y FROM dataset WHERE {quoted_a} IS NOT NULL AND {quoted_b} IS NOT NULL LIMIT {_SCATTER_SAMPLE_LIMIT}"
    try:
        result = get_duckdb_service().execute_query(dataset_id=dataset_id, query=sql, max_rows=settings.max_query_rows, timeout_seconds=settings.max_query_execution_seconds)
    except DatasetServiceError:
        logger.warning("Scatter sample query failed for %s/%s on dataset %s", column_a, column_b, dataset_id, exc_info=True)
        return None
    return result.rows


def chart_recommendation_node(state: DeepAnalysisState) -> dict:
    settings = get_settings()
    dataset_id = state["dataset_id"]
    budget = state["budget"]
    visualizations: list[VisualizationSpec] = []

    for trend in state.get("trends", []):
        visualizations.append(
            VisualizationSpec(
                type="line",
                title=f"{trend.metric_column} over time ({trend.granularity})",
                x_axis=AxisSpec(field="period"),
                y_axis=AxisSpec(field="value", label=trend.metric_column),
                data=[p.model_dump() for p in trend.periods],
            )
        )

    for segment in state.get("segments", []):
        visualizations.append(
            VisualizationSpec(
                type="bar",
                title=f"{segment.metric_column} by {segment.dimension_column}",
                x_axis=AxisSpec(field="segment", label=segment.dimension_column),
                y_axis=AxisSpec(field="metric_value", label=segment.metric_column),
                data=[s.model_dump() for s in segment.segments],
            )
        )

    for corr in state.get("correlations", []):
        if budget.status == "exceeded":
            break
        sample = _fetch_scatter_sample(dataset_id, corr.column_a, corr.column_b, settings)
        budget = _consume(budget, settings, state["start_time"], queries=1)
        if sample:
            visualizations.append(
                VisualizationSpec(
                    type="scatter",
                    title=f"{corr.column_a} vs {corr.column_b} (r={corr.coefficient})",
                    x_axis=AxisSpec(field="x", label=corr.column_a),
                    y_axis=AxisSpec(field="y", label=corr.column_b),
                    data=sample,
                )
            )

    if state.get("anomalies"):
        visualizations.append(VisualizationSpec(type="table", title="Detected anomalies", data=[a.model_dump() for a in state["anomalies"]]))

    quality = state.get("data_quality")
    if quality and quality.issues:
        visualizations.append(VisualizationSpec(type="table", title="Data quality issues", data=[i.model_dump() for i in quality.issues]))

    trace = [*state.get("trace", []), _trace("chart_recommendation", "completed", 0.0, f"{len(visualizations)} visualization(s) built")]
    return {"visualizations": visualizations, "budget": budget, "trace": trace}


# ====================================================================== #
# Finalize: assemble the stable DeepAnalysisResponse
# ====================================================================== #


def finalize_node(state: DeepAnalysisState) -> dict:
    budget = state["budget"]
    status = "budget_exceeded" if budget.status == "exceeded" else "completed"
    report = state.get("report")
    quality = state.get("data_quality")

    response = DeepAnalysisResponse(
        status=status,  # type: ignore[arg-type]
        summary=report.executive_summary if report else _fallback_summary(state),
        insights=state.get("insights", []),
        trends=state.get("trends", []),
        anomalies=state.get("anomalies", []),
        correlations=state.get("correlations", []),
        segments=state.get("segments", []),
        hypothesis_results=state.get("hypothesis_results", []),
        recommendations=state.get("recommendations", []),
        visualizations=state.get("visualizations", []),
        data_quality=quality.issues if quality else [],
        limitations=report.limitations if report else [],
        report=report,
        profile=state.get("profile"),
        trace=state.get("trace", []),
        budget=budget,
        review=state.get("review"),
        review_retry_count=state.get("review_retry_count", 0),
    )
    return {"final_response": response}
