"""
Lower-level tests for DuckDBService: row-cap truncation and query timeout.

These call the service directly with a synthetic in-memory DataFrame
(registered under a throwaway dataset_id) rather than going through the
upload API, so max_rows/timeout_seconds can be controlled precisely
without touching global settings.
"""

import uuid

import pandas as pd
import pytest

from app.services.duckdb_service import get_duckdb_service
from app.services.exceptions import QueryTimeoutError


@pytest.fixture
def small_dataset():
    service = get_duckdb_service()
    dataset_id = str(uuid.uuid4())
    dataframe = pd.DataFrame({"n": list(range(20))})
    service.register_dataset(dataset_id, dataframe)
    yield service, dataset_id
    service.unregister_dataset(dataset_id)


@pytest.fixture
def cross_join_dataset():
    """A dataset just large enough that a 4-way self cross join takes a
    fraction of a second -- long enough to reliably interrupt with a small
    timeout, short enough that the test suite stays fast even if the
    timeout somehow fails to fire."""

    service = get_duckdb_service()
    dataset_id = str(uuid.uuid4())
    dataframe = pd.DataFrame({"n": list(range(220))})
    service.register_dataset(dataset_id, dataframe)
    yield service, dataset_id
    service.unregister_dataset(dataset_id)


def test_execute_query_truncates_to_max_rows(small_dataset) -> None:
    service, dataset_id = small_dataset

    result = service.execute_query(dataset_id, "SELECT * FROM dataset", max_rows=5, timeout_seconds=5)

    assert result.row_count == 5
    assert len(result.rows) == 5
    assert result.truncated is True


def test_execute_query_reports_no_truncation_within_limit(small_dataset) -> None:
    service, dataset_id = small_dataset

    result = service.execute_query(dataset_id, "SELECT * FROM dataset", max_rows=100, timeout_seconds=5)

    assert result.row_count == 20
    assert result.truncated is False


def test_execute_query_respects_caller_limit_under_max_rows(small_dataset) -> None:
    service, dataset_id = small_dataset

    result = service.execute_query(
        dataset_id, "SELECT * FROM dataset LIMIT 3", max_rows=100, timeout_seconds=5
    )

    assert result.row_count == 3
    assert result.truncated is False


def test_execute_query_times_out_on_slow_query(cross_join_dataset) -> None:
    service, dataset_id = cross_join_dataset
    slow_query = "SELECT count(*) AS c FROM dataset a, dataset b, dataset c, dataset d"

    with pytest.raises(QueryTimeoutError):
        service.execute_query(dataset_id, slow_query, max_rows=10, timeout_seconds=0.1)


def test_execute_query_handles_null_and_missing_values(small_dataset) -> None:
    service, dataset_id = small_dataset

    result = service.execute_query(
        dataset_id, "SELECT NULL AS missing, n FROM dataset LIMIT 1", max_rows=10, timeout_seconds=5
    )

    assert result.rows[0]["missing"] is None
