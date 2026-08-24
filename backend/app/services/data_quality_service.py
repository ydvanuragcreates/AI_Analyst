"""
Phase 10, section 2: deterministic data-quality checks.

Every check here is a DuckDB aggregate query plus a fixed, explainable
threshold (IQR fences, a percentage cutoff) -- never an LLM judgment call.
Consumes a DatasetProfile (already computed once by profiling_service) so
this never re-derives missing counts, quantiles, etc. from scratch: it
reuses exactly the numbers already computed for the profile stage, the
same "reuse cached metadata, don't rescan" rule as section 40.

Only ever REPORTS issues (section 2: "Do not automatically modify the
dataset. Only report issues.") -- nothing here writes to the dataset or
DuckDB table.
"""

from __future__ import annotations

import logging

from app.core.config import Settings, get_settings
from app.schemas.deep_analysis import DataQualityIssue, DataQualityReport, DatasetProfile
from app.services.duckdb_service import DuckDBService, get_duckdb_service
from app.utils.sql_utils import quote_identifier as _quote

logger = logging.getLogger(__name__)

# Bounded, same reasoning as profiling_service's categorical top-value cap:
# each category-consistency check is its own GROUP BY scan.
_MAX_CATEGORY_CONSISTENCY_COLUMNS = 15


def _severity_from_ratio(ratio: float, *, high: float, medium: float) -> str:
    if ratio >= high:
        return "high"
    if ratio >= medium:
        return "medium"
    return "low"


class DataQualityService:
    def __init__(self, duckdb_service: DuckDBService, settings: Settings) -> None:
        self._duckdb = duckdb_service
        self._settings = settings

    def assess(self, dataset_id: str, profile: DatasetProfile) -> DataQualityReport:
        issues: list[DataQualityIssue] = []

        issues.extend(self._missing_value_issues(profile))
        duplicate_count = self._duplicate_row_count(dataset_id, profile.row_count)
        if duplicate_count > 0:
            issues.append(
                DataQualityIssue(
                    issue_type="duplicate_rows",
                    description=f"{duplicate_count} of {profile.row_count} row(s) are exact duplicates of another row.",
                    severity=_severity_from_ratio(
                        duplicate_count / profile.row_count if profile.row_count else 0.0, high=0.10, medium=0.02
                    ),
                    evidence={"duplicate_count": duplicate_count, "total_rows": profile.row_count},
                )
            )

        issues.extend(self._negative_value_issues(dataset_id, profile))
        issues.extend(self._outlier_issues(dataset_id, profile))
        issues.extend(self._inconsistent_category_issues(dataset_id, profile))
        issues.extend(self._narrow_time_range_issues(profile))

        return DataQualityReport(issues=issues, duplicate_row_count=duplicate_count, total_row_count=profile.row_count)

    # ------------------------------------------------------------------ #

    def _missing_value_issues(self, profile: DatasetProfile) -> list[DataQualityIssue]:
        issues = []
        for column in profile.columns:
            if column.missing_count <= 0:
                continue
            issues.append(
                DataQualityIssue(
                    issue_type="missing_values",
                    column=column.name,
                    description=f"'{column.name}' is missing {column.missing_percentage:.1f}% of values ({column.missing_count} of {profile.row_count} rows).",
                    severity=_severity_from_ratio(column.missing_percentage / 100.0, high=0.50, medium=0.10),
                    evidence={"missing_count": column.missing_count, "missing_percentage": column.missing_percentage},
                )
            )
        return issues

    def _duplicate_row_count(self, dataset_id: str, row_count: int) -> int:
        if row_count == 0:
            return 0
        sql = "SELECT COUNT(*) AS distinct_rows FROM (SELECT DISTINCT * FROM dataset) AS _distinct"
        result = self._execute(dataset_id, sql)
        distinct_rows = int(result.rows[0]["distinct_rows"]) if result.rows else row_count
        return max(0, row_count - distinct_rows)

    def _negative_value_issues(self, dataset_id: str, profile: DatasetProfile) -> list[DataQualityIssue]:
        candidates = [c for c in profile.columns if c.kind == "numeric" and c.min_value is not None and c.min_value < 0]
        if not candidates:
            return []

        select_parts = []
        for index, column in enumerate(candidates):
            quoted = _quote(column.name)
            select_parts.append(f"COUNT(*) FILTER (WHERE {quoted} < 0) AS c{index}_negative")
        result = self._execute(dataset_id, f"SELECT {', '.join(select_parts)} FROM dataset")
        row = result.rows[0] if result.rows else {}

        issues = []
        for index, column in enumerate(candidates):
            negative_count = int(row.get(f"c{index}_negative") or 0)
            if negative_count <= 0:
                continue
            ratio = negative_count / profile.row_count if profile.row_count else 0.0
            issues.append(
                DataQualityIssue(
                    issue_type="negative_values",
                    column=column.name,
                    description=(
                        f"'{column.name}' has a minimum value of {column.min_value:g} -- {negative_count} row(s) "
                        "are negative. Verify this is expected for this column."
                    ),
                    severity=_severity_from_ratio(ratio, high=0.50, medium=0.05) if ratio < 0.5 else "low",
                    evidence={"negative_count": negative_count, "min_value": column.min_value, "max_value": column.max_value},
                )
            )
        return issues

    def _outlier_issues(self, dataset_id: str, profile: DatasetProfile) -> list[DataQualityIssue]:
        candidates = [c for c in profile.columns if c.kind == "numeric" and c.q1 is not None and c.q3 is not None]
        if not candidates:
            return []

        multiplier = self._settings.outlier_iqr_multiplier
        bounds: dict[str, tuple[float, float]] = {}
        select_parts = []
        for index, column in enumerate(candidates):
            iqr = column.q3 - column.q1
            lower = column.q1 - multiplier * iqr
            upper = column.q3 + multiplier * iqr
            bounds[column.name] = (lower, upper)
            quoted = _quote(column.name)
            select_parts.append(f"COUNT(*) FILTER (WHERE {quoted} < {lower} OR {quoted} > {upper}) AS c{index}_outliers")
        result = self._execute(dataset_id, f"SELECT {', '.join(select_parts)} FROM dataset")
        row = result.rows[0] if result.rows else {}

        issues = []
        for index, column in enumerate(candidates):
            outlier_count = int(row.get(f"c{index}_outliers") or 0)
            if outlier_count <= 0:
                continue
            lower, upper = bounds[column.name]
            ratio = outlier_count / profile.row_count if profile.row_count else 0.0
            issues.append(
                DataQualityIssue(
                    issue_type="outliers",
                    column=column.name,
                    description=(
                        f"'{column.name}' has {outlier_count} value(s) outside the expected range "
                        f"[{lower:.2f}, {upper:.2f}] (Tukey {multiplier}x IQR fence)."
                    ),
                    severity=_severity_from_ratio(ratio, high=0.10, medium=0.02),
                    evidence={
                        "outlier_count": outlier_count,
                        "expected_range": [round(lower, 4), round(upper, 4)],
                        "actual_min": column.min_value,
                        "actual_max": column.max_value,
                        "method": "iqr",
                    },
                )
            )
        return issues

    def _inconsistent_category_issues(self, dataset_id: str, profile: DatasetProfile) -> list[DataQualityIssue]:
        candidates = [
            c.name
            for c in profile.columns
            if c.kind == "categorical" and not c.is_likely_identifier and not c.is_high_cardinality
        ][:_MAX_CATEGORY_CONSISTENCY_COLUMNS]

        issues = []
        for name in candidates:
            quoted = _quote(name)
            sql = (
                f"SELECT LOWER(TRIM({quoted})) AS norm, COUNT(DISTINCT {quoted}) AS variants "
                f"FROM dataset WHERE {quoted} IS NOT NULL GROUP BY norm HAVING COUNT(DISTINCT {quoted}) > 1 LIMIT 5"
            )
            try:
                result = self._execute(dataset_id, sql)
            except Exception:  # noqa: BLE001 -- one column's check failing must not fail the whole assessment
                logger.warning("Category-consistency check failed for column %r on dataset %s", name, dataset_id, exc_info=True)
                continue
            if not result.rows:
                continue
            issues.append(
                DataQualityIssue(
                    issue_type="inconsistent_categories",
                    column=name,
                    description=(
                        f"'{name}' has values that differ only by case/whitespace and are likely the same category "
                        f"(e.g. normalized value {result.rows[0]['norm']!r} has {result.rows[0]['variants']} distinct spellings)."
                    ),
                    severity="low",
                    evidence={"example_groups": result.rows},
                )
            )
        return issues

    def _narrow_time_range_issues(self, profile: DatasetProfile) -> list[DataQualityIssue]:
        from datetime import date as date_cls

        issues = []
        for column in profile.columns:
            if column.kind != "date" or not column.min_date or not column.max_date:
                continue
            try:
                span_days = (date_cls.fromisoformat(column.max_date[:10]) - date_cls.fromisoformat(column.min_date[:10])).days
            except ValueError:
                continue
            if span_days < 30:
                issues.append(
                    DataQualityIssue(
                        issue_type="narrow_time_range",
                        column=column.name,
                        description=(
                            f"'{column.name}' only spans {span_days} day(s) ({column.min_date} to {column.max_date}) "
                            "-- trend/seasonality conclusions from this range should be treated cautiously."
                        ),
                        severity="low",
                        evidence={"span_days": span_days, "min_date": column.min_date, "max_date": column.max_date},
                    )
                )
        return issues

    def _execute(self, dataset_id: str, sql: str):
        return self._duckdb.execute_query(
            dataset_id=dataset_id,
            query=sql,
            max_rows=self._settings.max_query_rows,
            timeout_seconds=self._settings.max_query_execution_seconds,
        )


def get_data_quality_service() -> DataQualityService:
    return DataQualityService(get_duckdb_service(), get_settings())
