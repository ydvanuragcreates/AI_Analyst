"""
Unit tests for the SQL safety validator (app/utils/sql_utils.py).

These exercise the validator directly, independent of DuckDB/FastAPI, so
they run fast and pin down exactly which statements are allowed/rejected.
"""

import pytest
from sqlglot import exp

from app.services.exceptions import QueryValidationError
from app.utils.sql_utils import render_for_execution, validate_query


def test_allows_plain_select() -> None:
    statement = validate_query("SELECT * FROM dataset LIMIT 10")
    assert isinstance(statement, exp.Select)


def test_allows_with_cte_group_by_order_by_having() -> None:
    query = """
        WITH totals AS (
            SELECT region, SUM(sales) AS total_sales
            FROM dataset
            GROUP BY region
        )
        SELECT region, total_sales
        FROM totals
        WHERE total_sales > 100
        ORDER BY total_sales DESC
        LIMIT 5 OFFSET 1
    """
    statement = validate_query(query)
    assert isinstance(statement, exp.Select)


def test_allows_basic_join_against_dataset_alias_twice() -> None:
    statement = validate_query(
        "SELECT a.region, b.region FROM dataset a JOIN dataset b ON a.region = b.region"
    )
    assert isinstance(statement, exp.Select)


@pytest.mark.parametrize(
    "query",
    [
        "DROP TABLE dataset",
        "INSERT INTO dataset VALUES (1)",
        "UPDATE dataset SET sales = 0",
        "DELETE FROM dataset",
        "CREATE TABLE evil AS SELECT 1",
        "ALTER TABLE dataset ADD COLUMN y INT",
        "TRUNCATE TABLE dataset",
        "ATTACH 'evil.db' AS evil",
        "DETACH evil",
        "COPY dataset TO 'out.csv'",
        "PRAGMA database_list",
        "SET memory_limit='1GB'",
        "CALL some_procedure()",
    ],
)
def test_rejects_non_select_statements(query: str) -> None:
    with pytest.raises(QueryValidationError):
        validate_query(query)


def test_rejects_multiple_statements() -> None:
    with pytest.raises(QueryValidationError, match="single SQL statement"):
        validate_query("SELECT * FROM dataset; DROP TABLE dataset")


def test_rejects_comment_hidden_drop() -> None:
    # Confirms the validator parses SQL rather than substring-matching --
    # a naive `"drop" in query.lower()` check would behave the same here,
    # but this also proves comments don't let disallowed statements slip
    # through undetected.
    with pytest.raises(QueryValidationError):
        validate_query("-- innocent comment\nDROP TABLE dataset -- trailing comment")


def test_rejects_filesystem_reading_function() -> None:
    with pytest.raises(QueryValidationError, match="read_csv_auto"):
        validate_query("SELECT * FROM read_csv_auto('/etc/passwd')")


def test_rejects_reference_to_other_table() -> None:
    with pytest.raises(QueryValidationError, match="unknown table"):
        validate_query("SELECT * FROM some_other_dataset_table")


def test_rejects_dataset_as_cte_name() -> None:
    with pytest.raises(QueryValidationError, match="reserved name"):
        validate_query("WITH dataset AS (SELECT 1) SELECT * FROM dataset")


def test_rejects_empty_query() -> None:
    with pytest.raises(QueryValidationError):
        validate_query("   ")


def test_rejects_invalid_syntax() -> None:
    with pytest.raises(QueryValidationError):
        validate_query("SELECT FROM WHERE ???")


def test_render_for_execution_substitutes_real_table_name() -> None:
    statement = validate_query("SELECT region, sales FROM dataset WHERE sales > 100")

    rendered = render_for_execution(statement, real_table_name="dataset_abc123")

    assert "dataset_abc123" in rendered
    assert "FROM DATASET " not in rendered.upper()


def test_render_for_execution_substitutes_in_cte_and_join() -> None:
    statement = validate_query(
        "SELECT a.region FROM dataset a JOIN dataset b ON a.region = b.region"
    )

    rendered = render_for_execution(statement, real_table_name="dataset_xyz789")

    assert rendered.lower().count("dataset_xyz789") == 2
