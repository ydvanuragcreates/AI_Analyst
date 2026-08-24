"""
Tests for Phase 10 section 2 (deterministic data-quality checks) --
services/data_quality_service.py -- against the golden_sales.csv fixture,
which plants exactly: 1 duplicate row, 1 missing value (discount), and 1
extreme outlier (transaction_amount=500)."""

import uuid

import pandas as pd
import pytest

from app.services.data_quality_service import get_data_quality_service
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


@pytest.fixture
def golden_sales_report(golden_sales_dataset):
    profile = get_profiling_service().profile_dataset(golden_sales_dataset)
    return get_data_quality_service().assess(golden_sales_dataset, profile)


def test_detects_planted_duplicate_row(golden_sales_report) -> None:
    assert golden_sales_report.duplicate_row_count == 1
    duplicate_issues = [i for i in golden_sales_report.issues if i.issue_type == "duplicate_rows"]
    assert len(duplicate_issues) == 1
    assert duplicate_issues[0].evidence["duplicate_count"] == 1


def test_detects_planted_missing_value(golden_sales_report) -> None:
    missing_issues = {i.column: i for i in golden_sales_report.issues if i.issue_type == "missing_values"}
    assert "discount" in missing_issues
    assert missing_issues["discount"].evidence["missing_count"] == 1


def test_detects_planted_extreme_outlier(golden_sales_report) -> None:
    outlier_issues = {i.column: i for i in golden_sales_report.issues if i.issue_type == "outliers"}
    assert "transaction_amount" in outlier_issues
    evidence = outlier_issues["transaction_amount"].evidence
    assert evidence["actual_max"] == 500
    assert evidence["outlier_count"] >= 1
    assert outlier_issues["transaction_amount"].severity in ("medium", "high")


def test_does_not_report_issues_for_clean_columns(golden_sales_report) -> None:
    # 'region' is a clean, consistent categorical column -- must not be
    # flagged for anything.
    assert not any(i.column == "region" for i in golden_sales_report.issues)


def test_data_quality_never_modifies_the_dataset(golden_sales_dataset, golden_sales_report) -> None:
    """Section 2: 'Do not automatically modify the dataset. Only report
    issues.' -- assert the underlying table is untouched by re-reading it
    directly after the assessment ran."""

    service = get_duckdb_service()
    sample = service.get_sample(golden_sales_dataset, rows=100)
    assert sample.row_count == 13


def test_negative_values_flagged_with_cautious_language() -> None:
    """The exact example from the Phase 10 spec: Age min=-4, max=137 ->
    'Potential data quality issue' -- not an assertion the data is wrong,
    a flag to verify."""

    dataset_id = str(uuid.uuid4())
    service = get_duckdb_service()
    dataframe = pd.DataFrame({"age": [25, 30, -4, 40, 137, 28, 32, 29, 31, 30]})
    service.register_dataset(dataset_id, dataframe)
    try:
        profile = get_profiling_service().profile_dataset(dataset_id)
        report = get_data_quality_service().assess(dataset_id, profile)
    finally:
        service.unregister_dataset(dataset_id)

    negative_issues = [i for i in report.issues if i.issue_type == "negative_values" and i.column == "age"]
    assert len(negative_issues) == 1
    assert negative_issues[0].evidence["min_value"] == -4
    assert "verify" in negative_issues[0].description.lower()


def test_inconsistent_category_spelling_detected() -> None:
    dataset_id = str(uuid.uuid4())
    service = get_duckdb_service()
    dataframe = pd.DataFrame({"region": ["Delhi", "delhi", "DELHI", "Mumbai", "Mumbai", "Mumbai"]})
    service.register_dataset(dataset_id, dataframe)
    try:
        profile = get_profiling_service().profile_dataset(dataset_id)
        report = get_data_quality_service().assess(dataset_id, profile)
    finally:
        service.unregister_dataset(dataset_id)

    inconsistent = [i for i in report.issues if i.issue_type == "inconsistent_categories"]
    assert len(inconsistent) == 1
    assert inconsistent[0].column == "region"
