"""
Tests for Phase 10 sections 7-10 (deterministic trend/anomaly/correlation/
segmentation) -- services/statistics_service.py -- against golden_sales.csv,
whose exact numbers are worked out by hand below.
"""

import uuid

import pandas as pd
import pytest

from app.services.duckdb_service import get_duckdb_service
from app.services.statistics_service import InsufficientDataError, get_statistics_service


@pytest.fixture
def golden_sales_dataset():
    service = get_duckdb_service()
    dataset_id = str(uuid.uuid4())
    dataframe = pd.read_csv("tests/fixtures/golden_sales.csv")
    service.register_dataset(dataset_id, dataframe)
    yield dataset_id
    service.unregister_dataset(dataset_id)


def test_detect_trend_finds_north_revenue_increasing(golden_sales_dataset) -> None:
    stats = get_statistics_service()
    # Combined (North+South) monthly revenue: 3000/2610/2695/2805/2915/3020
    # -- the fixture's one duplicate row is South's (smaller) January row,
    # so Jan is inflated but the overall trend is still net positive.
    trend = stats.detect_trend(golden_sales_dataset, "date", "revenue", granularity="month")

    assert trend.direction == "increasing"
    assert trend.periods[0].value == 2000 + 500 * 2  # North Jan + South Jan (doubled)
    assert trend.overall_absolute_change is not None


def test_detect_trend_raises_on_insufficient_periods(golden_sales_dataset) -> None:
    stats = get_statistics_service()
    with pytest.raises(InsufficientDataError):
        # Restrict to a single day's worth of data by using a granularity
        # so coarse relative to the range that... instead, directly prove
        # the guard by asking for a column with only one period: filter is
        # not exposed, so we simulate by using a 1-row dataset instead.
        one_row_id = str(uuid.uuid4())
        service = get_duckdb_service()
        service.register_dataset(one_row_id, pd.DataFrame({"d": ["2026-01-01"], "v": [5]}))
        try:
            stats.detect_trend(one_row_id, "d", "v", granularity="month")
        finally:
            service.unregister_dataset(one_row_id)


def test_detect_anomalies_finds_transaction_amount_spike(golden_sales_dataset) -> None:
    stats = get_statistics_service()
    anomalies = stats.detect_anomalies(golden_sales_dataset, "transaction_amount", method="iqr")

    assert len(anomalies) == 1
    assert anomalies[0].value == 500
    assert anomalies[0].severity == "high"
    assert anomalies[0].method == "iqr"


def test_detect_anomalies_finds_none_on_clean_column(golden_sales_dataset) -> None:
    stats = get_statistics_service()
    # 'discount' has no extreme outliers.
    anomalies = stats.detect_anomalies(golden_sales_dataset, "discount", method="iqr")
    assert anomalies == []


def test_calculate_correlation_discount_revenue_is_strong_positive(golden_sales_dataset) -> None:
    stats = get_statistics_service()
    result = stats.calculate_correlation(golden_sales_dataset, "discount", "revenue")

    assert result.coefficient > 0.8
    assert result.direction == "positive"
    assert result.strength in ("strong", "very_strong")
    assert result.sample_size == 12  # 13 rows, 1 discount value is null


def test_calculate_correlation_raises_on_too_few_paired_rows() -> None:
    stats = get_statistics_service()
    dataset_id = str(uuid.uuid4())
    service = get_duckdb_service()
    service.register_dataset(dataset_id, pd.DataFrame({"a": [1, 2, None], "b": [None, 2, 3]}))
    try:
        with pytest.raises(InsufficientDataError):
            stats.calculate_correlation(dataset_id, "a", "b")
    finally:
        service.unregister_dataset(dataset_id)


def test_segment_data_north_dominates_revenue_contribution(golden_sales_dataset) -> None:
    stats = get_statistics_service()
    result = stats.segment_data(golden_sales_dataset, "region", "revenue", aggregation="sum")

    by_segment = {s.segment: s for s in result.segments}
    assert by_segment["North"].metric_value == 2000 + 2100 + 2200 + 2300 + 2400 + 2500
    assert by_segment["South"].metric_value == 500 * 2 + 510 + 495 + 505 + 515 + 520  # duplicate Jan counted twice

    total_contribution = sum(s.contribution_percentage for s in result.segments)
    assert total_contribution == pytest.approx(100.0, abs=0.5)
    assert by_segment["North"].contribution_percentage > 75.0  # clearly disproportionate


def test_segment_data_raises_when_no_segments_found() -> None:
    stats = get_statistics_service()
    dataset_id = str(uuid.uuid4())
    service = get_duckdb_service()
    service.register_dataset(dataset_id, pd.DataFrame({"region": [None, None], "revenue": [1, 2]}))
    try:
        with pytest.raises(InsufficientDataError):
            stats.segment_data(dataset_id, "region", "revenue")
    finally:
        service.unregister_dataset(dataset_id)
