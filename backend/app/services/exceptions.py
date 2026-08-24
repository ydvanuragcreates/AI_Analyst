"""
Domain exceptions for the dataset and analytical-query services.

Routes never catch these directly. main.py registers a single FastAPI
exception handler for the DatasetServiceError family, so the HTTP-status
mapping lives in one place instead of being repeated in every route.
"""


class DatasetServiceError(Exception):
    """Base class for all dataset/query-service errors."""


class UnsupportedFileTypeError(DatasetServiceError):
    """The uploaded file's extension isn't one we support (400)."""


class EmptyFileError(DatasetServiceError):
    """The uploaded file has no content (400)."""


class FileTooLargeError(DatasetServiceError):
    """The uploaded file exceeds MAX_FILE_SIZE_MB (400)."""


class InvalidDatasetError(DatasetServiceError):
    """The file content can't be parsed as a valid dataset (400)."""


class DatasetTooLargeError(DatasetServiceError):
    """The parsed dataset exceeds Settings.max_dataset_rows/max_dataset_columns (400).

    Distinct from FileTooLargeError (which bounds raw upload BYTES, checked
    before parsing) -- this bounds the dataset's actual SHAPE after
    parsing, which the byte size alone doesn't determine (e.g. a small,
    highly-compressible file can still unpack into millions of rows)."""


class DatasetNotFoundError(DatasetServiceError):
    """No dataset matches the given dataset_id (404)."""


class DatasetStorageError(DatasetServiceError):
    """Reading/writing dataset files or metadata on disk failed (500)."""


class DuckDBRegistrationError(DatasetServiceError):
    """Registering/dropping a dataset's table in DuckDB failed (500)."""


class QueryValidationError(DatasetServiceError):
    """The submitted SQL failed the read-only safety validator (400)."""


class QueryExecutionError(DatasetServiceError):
    """DuckDB rejected an otherwise-validated query at execution time (400)."""


class QueryTimeoutError(DatasetServiceError):
    """The query exceeded MAX_QUERY_EXECUTION_SECONDS and was cancelled (408)."""
