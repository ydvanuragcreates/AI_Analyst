"""
Phase 10, sections 7-10: deterministic trend, anomaly, correlation, and
segmentation analysis.

Every number here comes from a DuckDB aggregate query, classified against
a fixed, explainable rule (IQR fences, a correlation-strength table, a
period-over-period percentage) -- nothing here is estimated by an LLM.
This is the "prefer deterministic computation for statistics/percentages/
aggregations/outlier calculations/correlations/trend calculations" rule
from the Phase 10 spec, applied directly: agent/deep_analysis/nodes.py
calls these functions and hands their typed output to an LLM only for
*interpretation*, never recomputes or re-derives the numbers itself.
"""

from __future__ import annotations

import logging
import statistics as pystats
from datetime import date as date_cls, timedelta

from app.core.config import Settings, get_settings
from app.schemas.deep_analysis import (
    AnomalyResult,
    CorrelationResult,
    SegmentResult,
    SegmentValue,
    TrendPeriod,
    TrendResult,
)
from app.services.duckdb_service import DuckDBService, get_duckdb_service
from app.services.exceptions import DatasetServiceError
from app.utils.sql_utils import quote_identifier as _quote

logger = logging.getLogger(__name__)

_SUDDEN_CHANGE_THRESHOLD_PCT = 50.0
_FLAT_THRESHOLD_PCT = 1.0


class InsufficientDataError(Exception):
    """Raised when a requested analysis genuinely cannot be computed from
    the available data (e.g. a trend with fewer than 2 periods, a
    correlation with too few paired rows) -- caught by the calling node
    and turned into a HypothesisResult(status='inconclusive') or a
    skipped analysis step, never a crash (section 32: partial failure
    tolerance) and never silently fabricated data."""


class StatisticsService:
    def __init__(self, duckdb_service: DuckDBService, settings: Settings) -> None:
        self._duckdb = duckdb_service
        self._settings = settings

    # ------------------------------------------------------------------ #
    # Section 7: trend detection
    # ------------------------------------------------------------------ #

    def detect_trend(
        self, dataset_id: str, date_column: str, metric_column: str, *, granularity: str = "month", aggregation: str = "sum"
    ) -> TrendResult:
        if granularity not in ("day", "week", "month"):
            raise ValueError(f"Unsupported granularity '{granularity}'")
        if aggregation not in ("sum", "avg", "count"):
            raise ValueError(f"Unsupported aggregation '{aggregation}'")

        date_expr = f"TRY_CAST({_quote(date_column)} AS DATE)"
        metric_expr = _quote(metric_column)
        agg_expr = "COUNT(*)" if aggregation == "count" else f"{aggregation.upper()}({metric_expr})"

        sql = (
            f"SELECT date_trunc('{granularity}', {date_expr}) AS period, {agg_expr} AS value "
            f"FROM dataset WHERE {date_expr} IS NOT NULL"
            + (f" AND {metric_expr} IS NOT NULL" if aggregation != "count" else "")
            + " GROUP BY 1 ORDER BY 1"
        )
        result = self._execute(dataset_id, sql)
        rows = [r for r in result.rows if r["value"] is not None]
        if len(rows) < 2:
            raise InsufficientDataError(
                f"Only {len(rows)} distinct {granularity}(s) of data -- at least 2 are needed to detect a trend"
            )

        periods: list[TrendPeriod] = []
        pct_changes: list[float] = []
        previous_value: float | None = None
        for row in rows:
            value = float(row["value"])
            absolute_change = None
            percentage_change = None
            if previous_value is not None:
                absolute_change = round(value - previous_value, 6)
                percentage_change = round((value - previous_value) / previous_value * 100.0, 4) if previous_value != 0 else None
                if percentage_change is not None:
                    pct_changes.append(percentage_change)
            periods.append(
                TrendPeriod(
                    period=str(row["period"]),
                    value=round(value, 6),
                    absolute_change=absolute_change,
                    percentage_change=percentage_change,
                )
            )
            previous_value = value

        direction = _classify_trend_direction(pct_changes)
        sudden_changes = [
            f"{p.period}: {p.percentage_change:+.1f}%"
            for p in periods
            if p.percentage_change is not None and abs(p.percentage_change) >= _SUDDEN_CHANGE_THRESHOLD_PCT
        ]
        accelerating = _is_accelerating(pct_changes)

        first_value, last_value = periods[0].value, periods[-1].value
        overall_absolute = round(last_value - first_value, 6)
        overall_pct = round((last_value - first_value) / first_value * 100.0, 4) if first_value != 0 else None

        return TrendResult(
            metric_column=metric_column,
            date_column=date_column,
            granularity=granularity,  # type: ignore[arg-type]
            direction=direction,
            periods=periods,
            overall_absolute_change=overall_absolute,
            overall_percentage_change=overall_pct,
            accelerating=accelerating,
            sudden_changes=sudden_changes,
        )

    # ------------------------------------------------------------------ #
    # Section 8: anomaly detection
    # ------------------------------------------------------------------ #

    def detect_anomalies(
        self, dataset_id: str, metric_column: str, *, method: str = "iqr", limit: int = 5
    ) -> list[AnomalyResult]:
        if method not in ("iqr", "zscore"):
            raise ValueError(f"Unsupported anomaly method '{method}'")

        quoted = _quote(metric_column)
        stats_sql = (
            f"SELECT AVG({quoted}) AS mean, STDDEV({quoted}) AS stddev, "
            f"PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {quoted}) AS q1, "
            f"PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {quoted}) AS q3, "
            f"COUNT({quoted}) AS n FROM dataset"
        )
        stats_row = self._execute(dataset_id, stats_sql).rows[0]
        n = int(stats_row.get("n") or 0)
        mean = stats_row.get("mean")
        stddev = stats_row.get("stddev")
        q1, q3 = stats_row.get("q1"), stats_row.get("q3")

        if n < self._settings.correlation_min_sample_size:
            raise InsufficientDataError(f"Only {n} non-null value(s) in '{metric_column}' -- too few to detect anomalies")

        if method == "iqr":
            if q1 is None or q3 is None:
                raise InsufficientDataError(f"Could not compute quantiles for '{metric_column}'")
            iqr = q3 - q1
            lower = q1 - self._settings.outlier_iqr_multiplier * iqr
            upper = q3 + self._settings.outlier_iqr_multiplier * iqr
            where = f"{quoted} < {lower} OR {quoted} > {upper}"
            expected_range = f"[{lower:.4g}, {upper:.4g}]"
        else:
            if stddev is None or stddev == 0:
                raise InsufficientDataError(f"'{metric_column}' has zero variance -- z-score anomaly detection is not meaningful")
            lower, upper = mean - 3 * stddev, mean + 3 * stddev
            where = f"ABS(({quoted} - {mean}) / {stddev}) > 3"
            expected_range = f"[{lower:.4g}, {upper:.4g}] (mean +/- 3 stddev)"

        rows_sql = (
            f"SELECT {quoted} AS value FROM dataset WHERE {where} "
            f"ORDER BY ABS({quoted} - {mean if mean is not None else 0}) DESC LIMIT {int(limit)}"
        )
        rows = self._execute(dataset_id, rows_sql).rows

        anomalies = []
        for row in rows:
            value = float(row["value"])
            if method == "iqr":
                distance = min(abs(value - upper), abs(value - lower)) if (value > upper or value < lower) else 0.0
                severity = "high" if distance > iqr else ("medium" if distance > 0.5 * iqr else "low")
            else:
                z = abs((value - mean) / stddev)
                severity = "high" if z > 5 else ("medium" if z > 4 else "low")
            anomalies.append(
                AnomalyResult(
                    metric_column=metric_column,
                    value=round(value, 6),
                    expected_range=expected_range,
                    method=method,  # type: ignore[arg-type]
                    severity=severity,  # type: ignore[arg-type]
                    context={},
                )
            )
        return anomalies

    # ------------------------------------------------------------------ #
    # Section 9: correlation
    # ------------------------------------------------------------------ #

    def calculate_correlation(self, dataset_id: str, column_a: str, column_b: str) -> CorrelationResult:
        quoted_a, quoted_b = _quote(column_a), _quote(column_b)
        sql = (
            f"SELECT CORR({quoted_a}, {quoted_b}) AS corr, "
            f"COUNT(*) FILTER (WHERE {quoted_a} IS NOT NULL AND {quoted_b} IS NOT NULL) AS n FROM dataset"
        )
        row = self._execute(dataset_id, sql).rows[0]
        n = int(row.get("n") or 0)
        coefficient = row.get("corr")

        if n < self._settings.correlation_min_sample_size or coefficient is None:
            raise InsufficientDataError(
                f"Only {n} paired non-null row(s) between '{column_a}' and '{column_b}' -- too few for a correlation"
            )

        coefficient = round(float(coefficient), 4)
        return CorrelationResult(
            column_a=column_a,
            column_b=column_b,
            coefficient=coefficient,
            strength=_classify_correlation_strength(coefficient),
            direction="positive" if coefficient > 0.01 else ("negative" if coefficient < -0.01 else "none"),
            sample_size=n,
        )

    # ------------------------------------------------------------------ #
    # Section 10: segmentation
    # ------------------------------------------------------------------ #

    def segment_data(
        self,
        dataset_id: str,
        dimension_column: str,
        metric_column: str,
        *,
        aggregation: str = "sum",
        date_column: str | None = None,
        limit: int = 10,
    ) -> SegmentResult:
        if aggregation not in ("sum", "avg", "count"):
            raise ValueError(f"Unsupported aggregation '{aggregation}'")

        quoted_dim = _quote(dimension_column)
        quoted_metric = _quote(metric_column)
        agg_expr = "COUNT(*)" if aggregation == "count" else f"{aggregation.upper()}({quoted_metric})"

        sql = (
            f"SELECT {quoted_dim} AS segment, {agg_expr} AS metric_value, COUNT(*) AS row_count "
            f"FROM dataset WHERE {quoted_dim} IS NOT NULL GROUP BY 1 ORDER BY metric_value DESC LIMIT {int(limit)}"
        )
        rows = self._execute(dataset_id, sql).rows
        if not rows:
            raise InsufficientDataError(f"No non-null segments found for '{dimension_column}'")

        if aggregation == "sum":
            total_row = self._execute(dataset_id, f"SELECT SUM({quoted_metric}) AS total FROM dataset").rows[0]
        else:
            total_row = {"total": sum(r["metric_value"] for r in rows) or None}
        total = total_row.get("total") or 0.0

        growth_by_segment = self._segment_growth(dataset_id, dimension_column, metric_column, date_column, aggregation) if date_column else {}

        segments = []
        for row in rows:
            value = float(row["metric_value"] or 0.0)
            contribution = (value / total * 100.0) if total else 0.0
            segments.append(
                SegmentValue(
                    segment=str(row["segment"]),
                    metric_value=round(value, 6),
                    row_count=int(row["row_count"]),
                    contribution_percentage=round(min(max(contribution, 0.0), 100.0), 2),
                    growth_percentage=growth_by_segment.get(str(row["segment"])),
                )
            )

        return SegmentResult(dimension_column=dimension_column, metric_column=metric_column, aggregation=aggregation, segments=segments)  # type: ignore[arg-type]

    def _segment_growth(
        self, dataset_id: str, dimension_column: str, metric_column: str, date_column: str, aggregation: str
    ) -> dict[str, float]:
        date_expr = f"TRY_CAST({_quote(date_column)} AS DATE)"
        bounds_row = self._execute(dataset_id, f"SELECT MIN({date_expr}) AS mn, MAX({date_expr}) AS mx FROM dataset").rows[0]
        min_date, max_date = bounds_row.get("mn"), bounds_row.get("mx")
        if not min_date or not max_date or min_date == max_date:
            return {}

        midpoint = _midpoint_date(min_date, max_date)
        quoted_dim = _quote(dimension_column)
        quoted_metric = _quote(metric_column)
        agg = "COUNT(*)" if aggregation == "count" else f"{aggregation.upper()}({quoted_metric})"

        sql = (
            f"SELECT {quoted_dim} AS segment, "
            f"{agg} FILTER (WHERE {date_expr} < DATE '{midpoint}') AS before_value, "
            f"{agg} FILTER (WHERE {date_expr} >= DATE '{midpoint}') AS after_value "
            f"FROM dataset WHERE {quoted_dim} IS NOT NULL GROUP BY 1"
        )
        try:
            rows = self._execute(dataset_id, sql).rows
        except DatasetServiceError:
            logger.warning("Segment growth query failed for %s/%s on dataset %s", dimension_column, metric_column, dataset_id, exc_info=True)
            return {}

        growth: dict[str, float] = {}
        for row in rows:
            before, after = row.get("before_value"), row.get("after_value")
            if before is None or after is None or before == 0:
                continue
            growth[str(row["segment"])] = round((after - before) / before * 100.0, 2)
        return growth

    def _execute(self, dataset_id: str, sql: str):
        return self._duckdb.execute_query(
            dataset_id=dataset_id,
            query=sql,
            max_rows=self._settings.max_query_rows,
            timeout_seconds=self._settings.max_query_execution_seconds,
        )


def _classify_trend_direction(pct_changes: list[float]) -> str:
    if not pct_changes:
        return "flat"
    if all(abs(change) < _FLAT_THRESHOLD_PCT for change in pct_changes):
        return "flat"
    positive = sum(1 for c in pct_changes if c > 0)
    negative = sum(1 for c in pct_changes if c < 0)
    avg = pystats.fmean(pct_changes)
    if positive / len(pct_changes) >= 0.7 and avg > 0:
        return "increasing"
    if negative / len(pct_changes) >= 0.7 and avg < 0:
        return "decreasing"
    return "volatile"


def _is_accelerating(pct_changes: list[float]) -> bool:
    if len(pct_changes) < 4:
        return False
    midpoint = len(pct_changes) // 2
    first_half_avg = pystats.fmean(pct_changes[:midpoint])
    second_half_avg = pystats.fmean(pct_changes[midpoint:])
    return second_half_avg > first_half_avg


def _classify_correlation_strength(coefficient: float) -> str:
    magnitude = abs(coefficient)
    if magnitude < 0.1:
        return "negligible"
    if magnitude < 0.3:
        return "weak"
    if magnitude < 0.5:
        return "moderate"
    if magnitude < 0.7:
        return "strong"
    return "very_strong"


def _midpoint_date(min_date, max_date) -> date_cls:
    min_d = min_date if isinstance(min_date, date_cls) else date_cls.fromisoformat(str(min_date)[:10])
    max_d = max_date if isinstance(max_date, date_cls) else date_cls.fromisoformat(str(max_date)[:10])
    return min_d + timedelta(days=(max_d - min_d).days // 2)


def get_statistics_service() -> StatisticsService:
    return StatisticsService(get_duckdb_service(), get_settings())
