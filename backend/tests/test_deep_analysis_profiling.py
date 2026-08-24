"""
Tests for Phase 10 section 1 (autonomous dataset profiling) --
services/profiling_service.py -- against a real DuckDB-registered golden
dataset (tests/fixtures/golden_sales.csv), never mocked: every assertion
here is a real, deterministic computation checked against numbers worked
out by hand from the fixture file.
"""

import uuid

import pandas as pd
import pytest

from app.services.duckdb_service import get_duckdb_service
from app.services.profiling_service import get_profiling_service


@pytest.fixture
def golden_sales_dataset():
    service = get_duckdb_service()
    dataset_id = str(uuid.uuid4())
    dataframe = pd.read_csv("tests/fixtures/golden_sales.csv")
    service.register_dataset(dataset_id, dataframe)
    yield dataset_id
    service.unregister_dataset(dataset_id)


def test_profile_reports_correct_row_and_column_counts(golden_sales_dataset) -> None:
    profile = get_profiling_service().profile_dataset(golden_sales_dataset)

    assert profile.row_count == 13  # 12 base rows + 1 planted duplicate
    assert profile.column_count == 5


def test_profile_classifies_columns_by_kind(golden_sales_dataset) -> None:
    profile = get_profiling_service().profile_dataset(golden_sales_dataset)

    assert set(profile.numeric_columns) == {"revenue", "discount", "transaction_amount"}
    assert profile.categorical_columns == ["region"]
    assert profile.date_columns == ["date"]


def test_profile_detects_date_like_varchar_column(golden_sales_dataset) -> None:
    """services/dataset_service.py never converts a date-looking CSV
    column to a real datetime dtype before DuckDB registration -- it
    arrives as VARCHAR. profiling_service must still detect it via
    TRY_CAST parseability (see profiling_service.py's module docstring)."""

    profile = get_profiling_service().profile_dataset(golden_sales_dataset)
    date_column = next(c for c in profile.columns if c.name == "date")

    assert date_column.kind == "date"
    assert date_column.dtype == "VARCHAR"
    assert date_column.min_date == "2026-01-01"
    assert date_column.max_date == "2026-06-01"
    assert date_column.is_likely_identifier is False


def test_profile_reports_missing_values_for_discount(golden_sales_dataset) -> None:
    profile = get_profiling_service().profile_dataset(golden_sales_dataset)
    discount_column = next(c for c in profile.columns if c.name == "discount")

    assert discount_column.missing_count == 1
    assert discount_column.missing_percentage == round(1 / 13 * 100, 2)
    assert "discount" in profile.missing_columns


def test_profile_computes_numeric_statistics_for_revenue(golden_sales_dataset) -> None:
    profile = get_profiling_service().profile_dataset(golden_sales_dataset)
    revenue_column = next(c for c in profile.columns if c.name == "revenue")

    values = [2000, 2100, 2200, 2300, 2400, 2500, 500, 510, 495, 505, 515, 520, 500]
    assert revenue_column.min_value == min(values)
    assert revenue_column.max_value == max(values)
    assert revenue_column.mean == pytest.approx(sum(values) / len(values), abs=0.01)


def test_profile_reports_categorical_top_values_for_region(golden_sales_dataset) -> None:
    profile = get_profiling_service().profile_dataset(golden_sales_dataset)
    region_column = next(c for c in profile.columns if c.name == "region")

    assert region_column.distinct_count == 2
    top_values = {v["value"]: v["count"] for v in region_column.top_values}
    assert top_values == {"South": 7, "North": 6}


def test_profile_does_not_scan_full_dataset_per_column(golden_sales_dataset, monkeypatch) -> None:
    """Section 40/41: profiling must use a small, FIXED number of
    aggregate queries, not one query per column (which would defeat the
    point of profiling large datasets cheaply)."""

    from app.services import profiling_service as module

    original_execute_query = module.get_duckdb_service().execute_query
    call_count = {"n": 0}

    def counting_execute_query(*args, **kwargs):
        call_count["n"] += 1
        return original_execute_query(*args, **kwargs)

    monkeypatch.setattr(module.get_duckdb_service(), "execute_query", counting_execute_query)

    get_profiling_service().profile_dataset(golden_sales_dataset)

    # 1 (row count + missing/distinct for all 5 columns) + 1 (numeric
    # stats for 3 columns) + 1 (date stats) + 1 (varchar-date detection
    # candidate check) + 1 (top-values for 'region') -- a small, bounded
    # number regardless of row count, not one query per column.
    assert call_count["n"] <= 6
