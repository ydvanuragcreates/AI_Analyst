"""
Phase 10, section 1: autonomous dataset profiling.

Every number in a DatasetProfile comes from a DuckDB aggregate query --
never pandas loaded into Python, never estimated, never asked of an LLM.
This is what "send only compact metadata to the model" (section 41) is
built on: a DatasetProfile is small (one row per column) regardless of
how many rows the dataset itself has, and it's computed with a small,
fixed number of full-table scans (2-3 queries total), not one query per
row or per LLM turn.

Goes through DuckDBService.execute_query exactly like every other query in
this project -- the same validated, table-aliased, row-capped path
execute_sql (the LangChain tool) and every workflow node already use.
Nothing here talks to DuckDB directly or bypasses sql_utils.validate_query.
"""

from __future__ import annotations

import logging

from app.core.config import Settings, get_settings
from app.schemas.deep_analysis import ColumnProfile, DatasetProfile
from app.services.duckdb_service import DuckDBService, get_duckdb_service
from app.utils.sql_utils import quote_identifier as _quote

logger = logging.getLogger(__name__)

_NUMERIC_TYPE_MARKERS = ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC", "HUGEINT")
_DATE_TYPE_MARKERS = ("DATE", "TIMESTAMP", "TIME")
_BOOLEAN_TYPE_MARKERS = ("BOOL",)

# Categorical top-value lookups are one GROUP BY scan each -- bounded so a
# very wide dataset can't turn one profile call into dozens of full scans.
_MAX_CATEGORICAL_TOP_VALUE_COLUMNS = 15


def _classify(dtype: str) -> str:
    upper = dtype.upper()
    if any(marker in upper for marker in _BOOLEAN_TYPE_MARKERS):
        return "boolean"
    if any(marker in upper for marker in _DATE_TYPE_MARKERS):
        return "date"
    if any(marker in upper for marker in _NUMERIC_TYPE_MARKERS):
        return "numeric"
    if upper in ("VARCHAR", "CHAR", "TEXT", "BLOB", "UUID"):
        return "categorical"
    return "other"


def _looks_like_identifier(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered == "id" or lowered.endswith("_id") or lowered.endswith("id")


class ProfilingService:
    """Builds a DatasetProfile for one dataset. Stateless -- every method
    takes dataset_id and reads DuckDBService fresh, same pattern as
    DuckDBService itself (no in-process cache; callers that want caching,
    e.g. the GET /api/v1/datasets/{id}/profile route, layer Redis on top)."""

    def __init__(self, duckdb_service: DuckDBService, settings: Settings) -> None:
        self._duckdb = duckdb_service
        self._settings = settings

    def profile_dataset(self, dataset_id: str) -> DatasetProfile:
        schema = self._duckdb.get_schema(dataset_id)
        columns_meta = [(c.name, c.type, _classify(c.type)) for c in schema.columns]

        row_count, missing_distinct = self._row_count_and_missing_distinct(dataset_id, columns_meta)

        # Dataset ingestion (services/dataset_service.py) never converts a
        # date-looking CSV/XLSX/JSON column to a real datetime dtype
        # before registering it in DuckDB -- it arrives here as VARCHAR,
        # same as any other text column. Detecting that is what makes
        # trend detection (section 7) possible at all for an ordinarily-
        # uploaded dataset, not just one that happened to already have a
        # DATE/TIMESTAMP column.
        categorical_names = [n for n, _, k in columns_meta if k == "categorical"]
        varchar_date_columns = self._detect_varchar_dates(dataset_id, categorical_names, missing_distinct, row_count)
        columns_meta = [
            (n, t, "date" if n in varchar_date_columns else k) for n, t, k in columns_meta
        ]

        numeric_stats = self._numeric_stats(dataset_id, [n for n, _, k in columns_meta if k == "numeric"])
        real_date_columns = [n for n, t, k in columns_meta if k == "date" and n not in varchar_date_columns]
        date_stats = self._date_stats(dataset_id, real_date_columns)
        date_stats.update(self._date_stats(dataset_id, varchar_date_columns, cast_from_varchar=True))

        profiles: list[ColumnProfile] = []
        for name, dtype, kind in columns_meta:
            missing_count, distinct_count = missing_distinct.get(name, (0, 0))
            missing_pct = (missing_count / row_count * 100.0) if row_count else 0.0
            # A date column is never treated as an identifier even when
            # every value happens to be distinct (e.g. one row per day) --
            # semantically it's a time dimension, not a row key, and
            # trend detection needs it findable via `date_columns`.
            is_identifier = kind != "date" and (
                (distinct_count == row_count and row_count > 0) or _looks_like_identifier(name)
            )
            is_high_cardinality = (
                not is_identifier
                and row_count > 0
                and distinct_count / row_count >= self._settings.high_cardinality_ratio_threshold
                and distinct_count > 20
            )

            column = ColumnProfile(
                name=name,
                dtype=dtype,
                kind=kind,  # type: ignore[arg-type]
                missing_count=missing_count,
                missing_percentage=round(missing_pct, 2),
                distinct_count=distinct_count,
                is_likely_identifier=is_identifier,
                is_high_cardinality=is_high_cardinality,
            )

            if kind == "numeric" and name in numeric_stats:
                stats = numeric_stats[name]
                column = column.model_copy(update=stats)
            if kind == "date" and name in date_stats:
                column = column.model_copy(update=date_stats[name])

            profiles.append(column)

        # Top values are fetched only for categorical columns worth
        # summarizing -- not identifiers, not already-flagged
        # high-cardinality free text, and capped in count (see module
        # constant) to bound total scans.
        categorical_for_top_values = [
            p.name for p in profiles if p.kind == "categorical" and not p.is_likely_identifier and not p.is_high_cardinality
        ][:_MAX_CATEGORICAL_TOP_VALUE_COLUMNS]
        top_values = self._top_values(dataset_id, categorical_for_top_values)
        if top_values:
            profiles = [
                p.model_copy(update={"top_values": top_values[p.name]}) if p.name in top_values else p
                for p in profiles
            ]

        return DatasetProfile(
            dataset_id=dataset_id,
            row_count=row_count,
            column_count=len(profiles),
            columns=profiles,
            numeric_columns=[p.name for p in profiles if p.kind == "numeric"],
            categorical_columns=[p.name for p in profiles if p.kind == "categorical"],
            date_columns=[p.name for p in profiles if p.kind == "date"],
            missing_columns=[p.name for p in profiles if p.missing_count > 0],
            high_cardinality_columns=[p.name for p in profiles if p.is_high_cardinality],
            identifier_columns=[p.name for p in profiles if p.is_likely_identifier],
        )

    # ------------------------------------------------------------------ #
    # One full-table scan for row count + every column's missing/distinct
    # count, regardless of how many columns exist.
    # ------------------------------------------------------------------ #

    def _row_count_and_missing_distinct(
        self, dataset_id: str, columns_meta: list[tuple[str, str, str]]
    ) -> tuple[int, dict[str, tuple[int, int]]]:
        if not columns_meta:
            return 0, {}

        select_parts = ["COUNT(*) AS row_count"]
        for index, (name, _, _) in enumerate(columns_meta):
            quoted = _quote(name)
            select_parts.append(f'COUNT(*) FILTER (WHERE {quoted} IS NULL) AS c{index}_missing')
            select_parts.append(f'COUNT(DISTINCT {quoted}) AS c{index}_distinct')

        sql = f"SELECT {', '.join(select_parts)} FROM dataset"
        result = self._execute(dataset_id, sql)
        if not result.rows:
            return 0, {}

        row = result.rows[0]
        row_count = int(row["row_count"] or 0)
        missing_distinct: dict[str, tuple[int, int]] = {}
        for index, (name, _, _) in enumerate(columns_meta):
            missing_distinct[name] = (
                int(row.get(f"c{index}_missing") or 0),
                int(row.get(f"c{index}_distinct") or 0),
            )
        return row_count, missing_distinct

    def _numeric_stats(self, dataset_id: str, numeric_columns: list[str]) -> dict[str, dict]:
        if not numeric_columns:
            return {}

        select_parts = []
        for index, name in enumerate(numeric_columns):
            quoted = _quote(name)
            select_parts.extend(
                [
                    f"MIN({quoted}) AS c{index}_min",
                    f"MAX({quoted}) AS c{index}_max",
                    f"AVG({quoted}) AS c{index}_mean",
                    f"MEDIAN({quoted}) AS c{index}_median",
                    f"STDDEV({quoted}) AS c{index}_stddev",
                    f"PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {quoted}) AS c{index}_q1",
                    f"PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {quoted}) AS c{index}_q3",
                ]
            )
        sql = f"SELECT {', '.join(select_parts)} FROM dataset"
        result = self._execute(dataset_id, sql)
        if not result.rows:
            return {}

        row = result.rows[0]
        stats: dict[str, dict] = {}
        for index, name in enumerate(numeric_columns):
            stats[name] = {
                "min_value": _as_float(row.get(f"c{index}_min")),
                "max_value": _as_float(row.get(f"c{index}_max")),
                "mean": _as_float(row.get(f"c{index}_mean")),
                "median": _as_float(row.get(f"c{index}_median")),
                "stddev": _as_float(row.get(f"c{index}_stddev")),
                "q1": _as_float(row.get(f"c{index}_q1")),
                "q3": _as_float(row.get(f"c{index}_q3")),
            }
        return stats

    def _date_stats(self, dataset_id: str, date_columns: list[str], *, cast_from_varchar: bool = False) -> dict[str, dict]:
        if not date_columns:
            return {}

        select_parts = []
        for index, name in enumerate(date_columns):
            quoted = _quote(name)
            expr = f"TRY_CAST({quoted} AS DATE)" if cast_from_varchar else quoted
            select_parts.append(f"MIN({expr}) AS c{index}_min")
            select_parts.append(f"MAX({expr}) AS c{index}_max")
        sql = f"SELECT {', '.join(select_parts)} FROM dataset"
        result = self._execute(dataset_id, sql)
        if not result.rows:
            return {}

        row = result.rows[0]
        stats: dict[str, dict] = {}
        for index, name in enumerate(date_columns):
            min_value = row.get(f"c{index}_min")
            max_value = row.get(f"c{index}_max")
            stats[name] = {
                "min_date": str(min_value) if min_value is not None else None,
                "max_date": str(max_value) if max_value is not None else None,
            }
        return stats

    def _detect_varchar_dates(
        self,
        dataset_id: str,
        categorical_columns: list[str],
        missing_distinct: dict[str, tuple[int, int]],
        row_count: int,
    ) -> list[str]:
        """Which VARCHAR columns actually hold dates, via TRY_CAST
        parseability against DuckDB's own date parser -- not a Python-side
        regex, and never assumed from the column name."""

        # A column needs at least 2 distinct non-null values to be worth
        # checking at all (an all-null or constant column can't be a
        # useful time dimension either way).
        candidates = [
            name
            for name in categorical_columns
            if missing_distinct.get(name, (0, 0))[1] > 1 and (row_count - missing_distinct.get(name, (0, 0))[0]) > 0
        ]
        if not candidates:
            return []

        select_parts = []
        for index, name in enumerate(candidates):
            quoted = _quote(name)
            select_parts.append(
                f"COUNT(*) FILTER (WHERE {quoted} IS NOT NULL AND TRY_CAST({quoted} AS DATE) IS NOT NULL) AS c{index}_parseable"
            )
        sql = f"SELECT {', '.join(select_parts)} FROM dataset"
        result = self._execute(dataset_id, sql)
        if not result.rows:
            return []

        row = result.rows[0]
        detected = []
        for index, name in enumerate(candidates):
            non_null = row_count - missing_distinct.get(name, (0, 0))[0]
            parseable = int(row.get(f"c{index}_parseable") or 0)
            if non_null > 0 and parseable / non_null >= 0.9:
                detected.append(name)
        return detected

    def _top_values(self, dataset_id: str, columns: list[str]) -> dict[str, list[dict]]:
        results: dict[str, list[dict]] = {}
        for name in columns:
            quoted = _quote(name)
            sql = (
                f"SELECT {quoted} AS value, COUNT(*) AS count FROM dataset "
                f"WHERE {quoted} IS NOT NULL GROUP BY {quoted} ORDER BY count DESC LIMIT 5"
            )
            try:
                result = self._execute(dataset_id, sql)
            except Exception:  # noqa: BLE001 -- one column's top-values query failing must not fail the whole profile
                logger.warning("Top-values query failed for column %r on dataset %s", name, dataset_id, exc_info=True)
                continue
            results[name] = [{"value": row["value"], "count": row["count"]} for row in result.rows]
        return results

    def _execute(self, dataset_id: str, sql: str):
        return self._duckdb.execute_query(
            dataset_id=dataset_id,
            query=sql,
            max_rows=self._settings.max_query_rows,
            timeout_seconds=self._settings.max_query_execution_seconds,
        )


def _as_float(value) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def get_profiling_service() -> ProfilingService:
    """Not @lru_cache'd (unlike get_duckdb_service): ProfilingService is
    stateless and cheap to construct, and always wraps the CURRENT
    get_duckdb_service()/get_settings() singletons rather than pinning
    them at first-call time."""

    return ProfilingService(get_duckdb_service(), get_settings())
