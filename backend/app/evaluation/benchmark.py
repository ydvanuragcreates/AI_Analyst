"""
Section 35/36: benchmark questions with known-correct expected answers,
run against the golden datasets through the REAL deterministic services
(profiling_service, data_quality_service, statistics_service) -- the
exact same code path agent/deep_analysis/nodes.py uses in production.

Each BenchmarkCase's `run` callable takes a dataset_id and returns
whatever value is being checked; `expected` is compared with `check`
(defaults to plain equality; a few cases use a tolerance or a set
predicate instead). This mirrors the Phase 10 spec's own examples
("What is the total revenue?" -> correct SQL + correct result; "Did
revenue increase?" -> correct time comparison; "Are discounts related to
sales?" -> correlation, NOT a causal claim) mapped onto this pipeline's
actual deterministic tools, since Phase 10's numbers come from
statistics_service, never from LLM-generated SQL.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from app.services.data_quality_service import get_data_quality_service
from app.services.duckdb_service import get_duckdb_service
from app.services.profiling_service import get_profiling_service
from app.services.statistics_service import get_statistics_service

GOLDEN_SALES_CSV = "tests/fixtures/golden_sales.csv"
GOLDEN_NO_DATE_CSV = "tests/fixtures/golden_no_date.csv"


@dataclass
class BenchmarkCase:
    question: str
    dataset_csv: str
    run: Callable[[str], Any]
    expected: Any
    check: Callable[[Any, Any], bool] = field(default=lambda actual, expected: actual == expected)


@dataclass
class BenchmarkResult:
    case: BenchmarkCase
    actual: Any
    passed: bool
    error: str | None = None


@dataclass
class BenchmarkReport:
    results: list[BenchmarkResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def accuracy(self) -> float:
        return self.passed_count / self.total if self.total else 0.0


def _total_revenue(dataset_id: str) -> float:
    profile = get_profiling_service().profile_dataset(dataset_id)
    revenue = next(c for c in profile.columns if c.name == "revenue")
    # mean * count reconstructs the sum from already-computed profile
    # stats -- exercising the SAME numbers a report/insight would use,
    # rather than issuing a brand-new ad hoc query.
    non_null = profile.row_count - revenue.missing_count
    return round(revenue.mean * non_null, 2)


def _top_region_by_revenue(dataset_id: str) -> str:
    result = get_statistics_service().segment_data(dataset_id, "region", "revenue", aggregation="sum")
    return result.segments[0].segment


def _revenue_trend_direction(dataset_id: str) -> str:
    return get_statistics_service().detect_trend(dataset_id, "date", "revenue", granularity="month").direction


def _discount_revenue_correlation_strength(dataset_id: str) -> str:
    return get_statistics_service().calculate_correlation(dataset_id, "discount", "revenue").strength


def _anomaly_count(dataset_id: str) -> int:
    return len(get_statistics_service().detect_anomalies(dataset_id, "transaction_amount", method="iqr"))


def _duplicate_row_count(dataset_id: str) -> int:
    profile = get_profiling_service().profile_dataset(dataset_id)
    return get_data_quality_service().assess(dataset_id, profile).duplicate_row_count


def _date_columns(dataset_id: str) -> list[str]:
    return get_profiling_service().profile_dataset(dataset_id).date_columns


BENCHMARK_CASES: list[BenchmarkCase] = [
    BenchmarkCase("What is the total revenue?", GOLDEN_SALES_CSV, _total_revenue, 13500 + 3545, lambda a, e: abs(a - e) < 1.0),
    BenchmarkCase("Which region has the highest revenue?", GOLDEN_SALES_CSV, _top_region_by_revenue, "North"),
    BenchmarkCase("Did revenue increase over time?", GOLDEN_SALES_CSV, _revenue_trend_direction, "increasing"),
    BenchmarkCase("Are discount and revenue related?", GOLDEN_SALES_CSV, _discount_revenue_correlation_strength, {"strong", "very_strong"}, lambda a, e: a in e),
    BenchmarkCase("Find anomalies in transaction_amount.", GOLDEN_SALES_CSV, _anomaly_count, 1),
    BenchmarkCase("How many duplicate rows exist?", GOLDEN_SALES_CSV, _duplicate_row_count, 1),
    # Adversarial (section 38): a dataset genuinely has no date column --
    # the correct behavior is to report that plainly, not fabricate one.
    BenchmarkCase("What date columns exist?", GOLDEN_NO_DATE_CSV, _date_columns, [], lambda a, e: a == e),
]


def run_benchmark(cases: list[BenchmarkCase] | None = None) -> BenchmarkReport:
    cases = cases if cases is not None else BENCHMARK_CASES
    service = get_duckdb_service()
    results: list[BenchmarkResult] = []

    dataset_ids: dict[str, str] = {}
    try:
        for case in cases:
            if case.dataset_csv not in dataset_ids:
                dataset_id = str(uuid.uuid4())
                service.register_dataset(dataset_id, pd.read_csv(case.dataset_csv))
                dataset_ids[case.dataset_csv] = dataset_id

            dataset_id = dataset_ids[case.dataset_csv]
            try:
                actual = case.run(dataset_id)
                passed = bool(case.check(actual, case.expected))
                results.append(BenchmarkResult(case=case, actual=actual, passed=passed))
            except Exception as exc:  # noqa: BLE001 -- a failing case is a result, not a crash
                results.append(BenchmarkResult(case=case, actual=None, passed=False, error=str(exc)))
    finally:
        for dataset_id in dataset_ids.values():
            service.unregister_dataset(dataset_id)

    return BenchmarkReport(results=results)
