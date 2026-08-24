"""
Dataset ingestion service.

Owns the full upload -> validate -> store -> inspect -> register -> metadata
pipeline. FastAPI routes call into this service and do nothing else; all
business logic (file validation, storage layout, pandas-based inspection,
type inference) lives here so it can be reused later — including as the
implementation behind future LangChain tools — without any FastAPI or
LangGraph awareness leaking into it. No OpenAI call happens anywhere in
this module: dataset inspection is intentionally pure, local processing.

Since Phase 3, this also drives DuckDB table registration through
DuckDBService (a separate service, not DuckDB logic inlined here — see
services/duckdb_service.py) so every successfully uploaded dataset is
immediately queryable via SQL.
"""

import io
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import UploadFile

from app.core.config import Settings, get_settings
from app.schemas.dataset import DatasetColumn, DatasetMetadata, DatasetPreview
from app.services.duckdb_service import DuckDBService, get_duckdb_service
from app.services.exceptions import (
    DatasetNotFoundError,
    DatasetServiceError,
    DatasetStorageError,
    DatasetTooLargeError,
    InvalidDatasetError,
    UnsupportedFileTypeError,
)
from app.utils.file_utils import (
    looks_like_xlsx,
    read_upload_within_limit,
    resolve_file_type,
    sanitize_filename,
)

logger = logging.getLogger(__name__)

METADATA_FILENAME = "metadata.json"
DATA_FILENAME_STEM = "original"


def infer_column_type(series: pd.Series) -> str:
    """Classify a column as one of: boolean, integer, float, datetime, string, object.

    Detection approach (deliberately simple, not semantic):
    1. Trust pandas' own dtype first — bool/int/float/datetime64 columns
       are already correctly typed by the CSV/XLSX/JSON parser.
    2. Anything left over is pandas' catch-all 'object' dtype (usually
       strings, or a mix). We try to parse its non-null values as dates;
       it's only classified 'datetime' if it isn't made of plain numeric
       strings (avoids misreading IDs like "2024" as years) and at least
       90% of values successfully parse.
    3. If every non-null value is a Python str, classify as 'string' —
       e.g. "Delhi" is just a string, not a recognized "city" type.
    4. Anything else (mixed types, nested lists/dicts from JSON) falls
       back to 'object'.
    """

    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        return "float"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"

    non_null = series.dropna()
    if non_null.empty:
        return "string"

    if _looks_like_datetime(non_null):
        return "datetime"

    if non_null.map(lambda value: isinstance(value, str)).all():
        return "string"

    return "object"


def _looks_like_datetime(non_null: pd.Series, threshold: float = 0.9) -> bool:
    if not non_null.map(lambda value: isinstance(value, str)).all():
        return False

    stripped = non_null.astype(str).str.strip()
    if stripped.str.fullmatch(r"-?\d+(\.\d+)?").all():
        return False  # plain numeric strings (e.g. IDs) are not dates

    parsed = pd.to_datetime(stripped, errors="coerce", format="mixed")
    return bool(parsed.notna().mean() >= threshold)


def _dataframe_preview(dataframe: pd.DataFrame, row_count: int) -> list[dict[str, Any]]:
    """Convert the first `row_count` rows to plain JSON-safe dicts.

    Goes through to_json()/json.loads() rather than to_dict(orient="records")
    because pandas' JSON encoder already turns NaN -> null, Timestamps ->
    ISO strings, and numpy scalar types -> native Python — reimplementing
    that by hand is exactly the kind of edge-case-prone code to avoid.
    """

    head = dataframe.head(row_count)
    return json.loads(head.to_json(orient="records", date_format="iso"))


class DatasetService:
    """Handles dataset upload, storage, inspection, and retrieval."""

    def __init__(self, settings: Settings, duckdb_service: DuckDBService) -> None:
        self._storage_root = settings.dataset_storage_root
        self._preview_rows = settings.dataset_preview_rows
        self._max_file_size_bytes = settings.max_file_size_bytes
        self._max_dataset_rows = settings.max_dataset_rows
        self._max_dataset_columns = settings.max_dataset_columns
        self._storage_root.mkdir(parents=True, exist_ok=True)
        self._duckdb_service = duckdb_service

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def upload_dataset(self, file: UploadFile) -> DatasetMetadata:
        original_filename = sanitize_filename(file.filename)
        file_type = resolve_file_type(original_filename)

        content = await read_upload_within_limit(file, self._max_file_size_bytes)
        self._validate_content_matches_type(file_type, content)
        dataframe = self._parse_dataframe(file_type, content)
        self._validate_shape_within_limits(dataframe)

        dataset_id = str(uuid.uuid4())
        dataset_dir = self._dataset_dir(dataset_id)
        try:
            dataset_dir.mkdir(parents=True, exist_ok=False)
            (dataset_dir / f"{DATA_FILENAME_STEM}.{file_type}").write_bytes(content)
        except OSError as exc:
            logger.error("Failed to store dataset %s", dataset_id, exc_info=exc)
            raise DatasetStorageError("Failed to store the uploaded dataset") from exc

        # Register in DuckDB before writing metadata.json: metadata.json's
        # existence is what list/get/delete treat as "this dataset is
        # fully ready". If registration fails, roll back the file we just
        # wrote rather than leaving a half-uploaded dataset that would
        # show up as successful but isn't queryable.
        try:
            self._duckdb_service.register_dataset(dataset_id, dataframe)
        except DatasetServiceError:
            logger.error("Rolling back dataset %s after DuckDB registration failure", dataset_id)
            shutil.rmtree(dataset_dir, ignore_errors=True)
            raise

        metadata = self._build_metadata(
            dataset_id=dataset_id,
            filename=original_filename,
            file_type=file_type,
            file_size=len(content),
            dataframe=dataframe,
        )
        try:
            self._write_metadata(dataset_dir, metadata)
        except DatasetServiceError:
            logger.error(
                "Rolling back dataset %s and its DuckDB table after metadata write failure", dataset_id
            )
            self._duckdb_service.unregister_dataset(dataset_id)
            shutil.rmtree(dataset_dir, ignore_errors=True)
            raise

        return metadata

    def list_datasets(self) -> list[DatasetMetadata]:
        if not self._storage_root.exists():
            return []

        datasets: list[DatasetMetadata] = []
        for entry in sorted(self._storage_root.iterdir()):
            if not entry.is_dir():
                continue
            metadata = self._read_metadata(entry)
            if metadata is not None:
                datasets.append(metadata)

        datasets.sort(key=lambda item: item.uploaded_at, reverse=True)
        return datasets

    def get_dataset(self, dataset_id: str) -> DatasetMetadata:
        dataset_dir = self._existing_dataset_dir(dataset_id)
        metadata = self._read_metadata(dataset_dir)
        if metadata is None:
            raise DatasetNotFoundError(f"Dataset '{dataset_id}' was not found")
        return metadata

    def delete_dataset(self, dataset_id: str) -> None:
        dataset_dir = self._existing_dataset_dir(dataset_id)
        self._duckdb_service.unregister_dataset(dataset_id)
        try:
            shutil.rmtree(dataset_dir)
        except OSError as exc:
            logger.error("Failed to delete dataset %s", dataset_id, exc_info=exc)
            raise DatasetStorageError("Failed to delete the dataset") from exc

    # ------------------------------------------------------------------ #
    # Storage path helpers. dataset_id is always either a UUID4 we
    # generated ourselves (upload) or validated against the UUID format
    # before touching the filesystem (get/delete) — this is what keeps
    # path construction safe from traversal, regardless of client input.
    # ------------------------------------------------------------------ #

    def _dataset_dir(self, dataset_id: str) -> Path:
        return self._storage_root / dataset_id

    def _existing_dataset_dir(self, dataset_id: str) -> Path:
        try:
            validated_id = str(uuid.UUID(dataset_id))
        except (ValueError, AttributeError, TypeError):
            raise DatasetNotFoundError(f"Dataset '{dataset_id}' was not found") from None

        dataset_dir = self._dataset_dir(validated_id)
        if not dataset_dir.is_dir():
            raise DatasetNotFoundError(f"Dataset '{dataset_id}' was not found")
        return dataset_dir

    # ------------------------------------------------------------------ #
    # Metadata persistence: a JSON sidecar file per dataset. No database
    # yet (that's a later phase) but unlike a pure in-memory dict, it
    # survives a process restart.
    # ------------------------------------------------------------------ #

    def _write_metadata(self, dataset_dir: Path, metadata: DatasetMetadata) -> None:
        metadata_path = dataset_dir / METADATA_FILENAME
        try:
            metadata_path.write_text(metadata.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to write metadata for dataset %s", metadata.dataset_id, exc_info=exc)
            raise DatasetStorageError("Failed to store dataset metadata") from exc

    def _read_metadata(self, dataset_dir: Path) -> DatasetMetadata | None:
        metadata_path = dataset_dir / METADATA_FILENAME
        if not metadata_path.exists():
            return None
        try:
            return DatasetMetadata.model_validate_json(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.error("Failed to read metadata at %s", metadata_path, exc_info=exc)
            return None

    # ------------------------------------------------------------------ #
    # Content validation
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_content_matches_type(file_type: str, content: bytes) -> None:
        """A lightweight defense against a mislabeled extension.

        CSV/JSON are text formats without a reliable magic number, so for
        those we rely on the parser itself (pandas / json.loads) to reject
        bad content — a genuine binary file simply won't parse as CSV/JSON
        text. XLSX is a zip archive with a well-defined signature, so it's
        checked directly before even attempting to parse.
        """

        if file_type == "xlsx" and not looks_like_xlsx(content):
            raise InvalidDatasetError(
                "File content does not match the .xlsx format (invalid archive signature)"
            )

    def _validate_shape_within_limits(self, dataframe: pd.DataFrame) -> None:
        """MAX_FILE_SIZE_MB bounds the raw upload's bytes; this bounds the
        parsed dataset's actual SHAPE -- a small, highly-compressible file
        can still unpack into an unbounded number of rows/columns, which
        the byte-size check alone never catches (production-readiness
        audit finding). Checked before anything is registered in DuckDB
        or written to disk, so a rejected upload leaves nothing behind.
        """

        if len(dataframe) > self._max_dataset_rows:
            raise DatasetTooLargeError(
                f"Dataset has {len(dataframe)} rows, exceeding the maximum of {self._max_dataset_rows}"
            )
        if len(dataframe.columns) > self._max_dataset_columns:
            raise DatasetTooLargeError(
                f"Dataset has {len(dataframe.columns)} columns, exceeding the maximum of {self._max_dataset_columns}"
            )

    # ------------------------------------------------------------------ #
    # Parsing
    # ------------------------------------------------------------------ #

    def _parse_dataframe(self, file_type: str, content: bytes) -> pd.DataFrame:
        try:
            if file_type == "csv":
                return pd.read_csv(io.BytesIO(content))
            if file_type == "xlsx":
                return pd.read_excel(io.BytesIO(content), engine="openpyxl")
            if file_type == "json":
                return self._parse_json(content)
        except InvalidDatasetError:
            raise
        except Exception as exc:  # pandas/openpyxl raise many distinct error types
            logger.info("Failed to parse uploaded %s file: %s", file_type, exc)
            raise InvalidDatasetError(
                f"The uploaded {file_type.upper()} file is corrupted or unreadable"
            ) from exc

        raise UnsupportedFileTypeError(f"Unsupported file type '{file_type}'")

    @staticmethod
    def _parse_json(content: bytes) -> pd.DataFrame:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise InvalidDatasetError(
                f"Invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
            ) from exc

        if not isinstance(data, list):
            raise InvalidDatasetError(
                f"Unsupported JSON structure: expected a top-level array of objects, got {type(data).__name__}"
            )
        if not data:
            raise InvalidDatasetError("JSON array is empty")
        if not all(isinstance(item, dict) for item in data):
            raise InvalidDatasetError("Unsupported JSON structure: every array item must be an object")

        return pd.DataFrame(data)

    # ------------------------------------------------------------------ #
    # Metadata construction
    # ------------------------------------------------------------------ #

    def _build_metadata(
        self,
        *,
        dataset_id: str,
        filename: str,
        file_type: str,
        file_size: int,
        dataframe: pd.DataFrame,
    ) -> DatasetMetadata:
        columns = [
            DatasetColumn(
                name=str(column),
                data_type=infer_column_type(dataframe[column]),
                nullable=bool(dataframe[column].isna().any()),
            )
            for column in dataframe.columns
        ]
        missing_values = {str(column): int(dataframe[column].isna().sum()) for column in dataframe.columns}

        return DatasetMetadata(
            dataset_id=dataset_id,
            filename=filename,
            file_type=file_type,
            file_size=file_size,
            row_count=len(dataframe),
            column_count=len(dataframe.columns),
            columns=columns,
            missing_values=missing_values,
            preview=DatasetPreview(_dataframe_preview(dataframe, self._preview_rows)),
            uploaded_at=datetime.now(timezone.utc),
        )


@lru_cache
def get_dataset_service() -> DatasetService:
    """FastAPI dependency: a process-wide DatasetService instance.

    Same caching pattern as get_chat_model() / get_agent_graph() in
    Phase 1 — a singleton via lru_cache rather than a mutable global.
    """

    return DatasetService(get_settings(), get_duckdb_service())
