"""
DuckDB-backed analytical query engine.

Owns the single embedded DuckDB database used to make uploaded datasets
queryable via SQL: registering a dataset's already-parsed DataFrame as a
table, looking up its schema, running validated read-only queries against
it, and dropping the table on dataset deletion. Independent of FastAPI —
everything here is plain Python + pandas + duckdb (Pydantic schemas are
used as plain typed return values, the same pattern DatasetService uses).

Concurrency note (Phase 9 production-readiness audit -- CRITICAL finding):
DuckDB is an embedded, single-writer-process database, like SQLite. The
previous design held ONE persistent `duckdb.connect()` open for a
process's entire lifetime, which is safe within one process (guarded by
`_thread_lock`) but breaks the moment a SECOND OS process also opens the
same file -- which is exactly what happens in the docker-compose
deployment, where `api` and `worker` are separate containers that both
call `get_duckdb_service()`. Confirmed empirically during this project's
own Phase 8 testing: a second process opening the same DuckDB file raises
"IO Error: Cannot open file ... already open in another process"
immediately, not just under rare contention.

Fixed by making every connection SHORT-LIVED (opened, used, and closed
within one operation) and wrapping that whole span in an OS-level,
cross-process file lock (`filelock`, a small, dependency-free primitive
directly justified by this specific problem -- not "unnecessary
infrastructure"). This is what actually makes "FastAPI dataset upload"
and "a Celery worker running an analysis" safe to run as genuinely
separate processes against the same file: only one of them ever has the
file open at a time, enforced by the OS, not by convention. See
tests/test_duckdb_concurrency.py for a same-process, multi-thread
reproduction of the race this prevents.
"""

import logging
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from math import isnan
from typing import Any, Iterator

import duckdb
import pandas as pd
from filelock import FileLock, Timeout as FileLockTimeout

from app.core.config import Settings, get_settings
from app.schemas.analysis import DatasetSchema, QueryResult, SampleDataResponse, SchemaColumn
from app.services.exceptions import (
    DatasetNotFoundError,
    DuckDBRegistrationError,
    QueryExecutionError,
    QueryTimeoutError,
)
from app.utils.sql_utils import render_for_execution, validate_query

logger = logging.getLogger(__name__)

_TABLE_NAME_PATTERN = re.compile(r"^dataset_[0-9a-f]{32}$")


class DuckDBService:
    """Handles dataset table registration and query execution against DuckDB."""

    def __init__(self, settings: Settings) -> None:
        self._db_path = settings.duckdb_database_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # `filelock` uses OS-level advisory locks (fcntl on POSIX,
        # msvcrt on Windows) that the OS releases automatically if the
        # holding process dies -- a crashed worker can't permanently wedge
        # this lock, only a genuinely hung one (bounded by lock_timeout).
        self._file_lock = FileLock(f"{self._db_path}.lock", timeout=settings.duckdb_lock_timeout_seconds)
        # Kept in addition to the file lock: cheap, avoids two threads in
        # THIS process both queuing for the OS lock when they could just
        # take turns in-process first.
        self._thread_lock = threading.Lock()

    @contextmanager
    def _connection(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """Acquire exclusive cross-process access, open a fresh
        connection for the duration of one operation, and always close +
        release -- see the module docstring for why this replaced a
        single persistent connection."""

        with self._thread_lock:
            try:
                with self._file_lock:
                    connection = duckdb.connect(str(self._db_path))
                    try:
                        yield connection
                    finally:
                        connection.close()
            except FileLockTimeout as exc:
                raise QueryTimeoutError(
                    "The analytical database is busy with another operation; please retry"
                ) from exc

    # ------------------------------------------------------------------ #
    # Table naming: a pure, deterministic function of dataset_id, not a
    # separately stored mapping. It's derived only from the UUID's hex
    # digits, so user input can never influence the resulting SQL
    # identifier, and there's no extra state that could drift from
    # reality (e.g. survive a restart incorrectly, or go stale after a
    # delete). "Is this dataset registered?" is answered by asking
    # DuckDB's own catalog (see _table_exists_locked), not a cache.
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_table_name(dataset_id: str) -> str:
        try:
            return f"dataset_{uuid.UUID(dataset_id).hex}"
        except (ValueError, AttributeError, TypeError):
            raise DatasetNotFoundError(f"Dataset '{dataset_id}' was not found") from None

    @staticmethod
    def _assert_safe_table_name(table_name: str) -> None:
        # Defense in depth: every call site derives table_name from
        # get_table_name(), but this guards against a future bug that
        # accidentally interpolates a raw dataset_id into SQL text.
        if not _TABLE_NAME_PATTERN.fullmatch(table_name):
            raise DuckDBRegistrationError(f"Refusing to use unsafe table name '{table_name}'")

    # ------------------------------------------------------------------ #
    # Registration / deregistration
    # ------------------------------------------------------------------ #

    def register_dataset(self, dataset_id: str, dataframe: pd.DataFrame) -> str:
        """Materialize an already-parsed DataFrame as a DuckDB table. Returns the table name."""

        table_name = self.get_table_name(dataset_id)
        self._assert_safe_table_name(table_name)
        view_name = f"_import_{table_name}"

        with self._connection() as connection:
            try:
                connection.register(view_name, dataframe)
                connection.execute(f'CREATE OR REPLACE TABLE "{table_name}" AS SELECT * FROM "{view_name}"')
            except Exception as exc:
                logger.error("Failed to register dataset %s in DuckDB", dataset_id, exc_info=exc)
                raise DuckDBRegistrationError(
                    f"Failed to register dataset '{dataset_id}' in DuckDB"
                ) from exc
            finally:
                connection.unregister(view_name)

        return table_name

    def unregister_dataset(self, dataset_id: str) -> None:
        """Drop a dataset's table. Safe to call even if it was never registered."""

        table_name = self.get_table_name(dataset_id)
        self._assert_safe_table_name(table_name)
        with self._connection() as connection:
            try:
                connection.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            except Exception as exc:
                logger.error("Failed to drop DuckDB table for dataset %s", dataset_id, exc_info=exc)
                raise DuckDBRegistrationError(
                    f"Failed to remove dataset '{dataset_id}' from DuckDB"
                ) from exc

    # ------------------------------------------------------------------ #
    # Schema / sample
    # ------------------------------------------------------------------ #

    def get_schema(self, dataset_id: str) -> DatasetSchema:
        table_name = self.get_table_name(dataset_id)
        self._assert_safe_table_name(table_name)

        with self._connection() as connection:
            if not self._table_exists(connection, table_name):
                raise DatasetNotFoundError(f"Dataset '{dataset_id}' was not found")
            described = connection.execute(f'DESCRIBE "{table_name}"').fetchall()

        columns = [SchemaColumn(name=row[0], type=row[1]) for row in described]
        return DatasetSchema(dataset_id=dataset_id, table_name=table_name, columns=columns)

    def get_sample(self, dataset_id: str, rows: int) -> SampleDataResponse:
        table_name = self.get_table_name(dataset_id)
        self._assert_safe_table_name(table_name)

        with self._connection() as connection:
            if not self._table_exists(connection, table_name):
                raise DatasetNotFoundError(f"Dataset '{dataset_id}' was not found")
            cursor = connection.execute(f'SELECT * FROM "{table_name}" LIMIT {int(rows)}')
            fetched = cursor.fetchall()
            columns = [description[0] for description in cursor.description]

        result_rows = [_row_to_dict(columns, row) for row in fetched]
        return SampleDataResponse(
            dataset_id=dataset_id, columns=columns, rows=result_rows, row_count=len(result_rows)
        )

    @staticmethod
    def _table_exists(connection: duckdb.DuckDBPyConnection, table_name: str) -> bool:
        result = connection.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table_name]
        ).fetchone()
        return result is not None

    # ------------------------------------------------------------------ #
    # Query execution
    # ------------------------------------------------------------------ #

    def execute_query(
        self, dataset_id: str, query: str, max_rows: int, timeout_seconds: float
    ) -> QueryResult:
        table_name = self.get_table_name(dataset_id)
        self._assert_safe_table_name(table_name)

        # Validated before opening a connection -- pure parsing, no DB
        # access. The caller's SQL only ever names the fixed alias
        # `dataset` (see sql_utils.DATASET_TABLE_ALIAS); render_for_execution
        # swaps that for the real internal table name via an AST rewrite,
        # so the query text itself never has a way to reference any other
        # dataset's table.
        validated = validate_query(query)
        rendered_sql = render_for_execution(validated, real_table_name=table_name)

        # Wrapped as a subquery so MAX_QUERY_ROWS is enforced regardless of
        # what LIMIT (if any) the caller's own SQL specifies. Fetching
        # max_rows + 1 (instead of an unbounded fetch) is what lets us
        # detect truncation without ever pulling more than one row over
        # the configured cap into Python memory.
        wrapped_sql = f"SELECT * FROM ({rendered_sql}) AS _validated_query LIMIT {max_rows + 1}"

        started_at = time.perf_counter()
        with self._connection() as connection:
            if not self._table_exists(connection, table_name):
                raise DatasetNotFoundError(f"Dataset '{dataset_id}' was not found")

            timer = threading.Timer(timeout_seconds, connection.interrupt)
            timer.start()
            try:
                cursor = connection.execute(wrapped_sql)
                fetched = cursor.fetchall()
                columns = [description[0] for description in cursor.description]
            except duckdb.InterruptException as exc:
                raise QueryTimeoutError(
                    f"Query exceeded the {timeout_seconds}s execution limit"
                ) from exc
            except Exception as exc:
                raise QueryExecutionError(f"Query execution failed: {exc}") from exc
            finally:
                timer.cancel()
        elapsed_ms = (time.perf_counter() - started_at) * 1000

        truncated = len(fetched) > max_rows
        limited_rows = fetched[:max_rows]
        result_rows = [_row_to_dict(columns, row) for row in limited_rows]

        return QueryResult(
            dataset_id=dataset_id,
            columns=columns,
            rows=result_rows,
            row_count=len(result_rows),
            execution_time_ms=round(elapsed_ms, 3),
            truncated=truncated,
        )


def _row_to_dict(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    return {column: _json_safe_value(value) for column, value in zip(columns, row)}


def _json_safe_value(value: Any) -> Any:
    """Convert one DuckDB result value into a JSON-serializable Python value."""

    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and isnan(value):
        return None
    return value


@lru_cache
def get_duckdb_service() -> DuckDBService:
    """FastAPI dependency: a process-wide DuckDBService instance (one connection, one lock)."""

    return DuckDBService(get_settings())
