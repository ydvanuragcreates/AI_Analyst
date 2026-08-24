"""
Generic, dataset-agnostic file-handling helpers.

Filename sanitization, extension allowlisting, size-bounded reading, and
lightweight content sniffing. Nothing here knows about pandas or dataset
metadata — that domain logic belongs to DatasetService.
"""

from pathlib import PurePosixPath

from fastapi import UploadFile

from app.services.exceptions import EmptyFileError, FileTooLargeError, UnsupportedFileTypeError

EXTENSION_TO_FILE_TYPE = {
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".json": "json",
}

# .xlsx files are zip archives; every real one starts with this signature.
XLSX_MAGIC_BYTES = b"PK\x03\x04"

_CHUNK_SIZE = 1024 * 1024  # 1 MB


def sanitize_filename(filename: str | None) -> str:
    """Strip any directory components, keeping only the base filename.

    Guards against a client sending a filename like "../../evil.csv" —
    we only ever use this value as a display string in metadata, never to
    build a filesystem path, but it's sanitized regardless.
    """

    if not filename:
        return "upload"
    return PurePosixPath(filename.replace("\\", "/")).name or "upload"


def resolve_file_type(filename: str) -> str:
    """Map a sanitized filename's extension to a supported file type.

    Raises UnsupportedFileTypeError for anything outside the allowlist —
    the extension is only the *first* check; actual content is verified
    separately (see looks_like_xlsx and DatasetService._parse_dataframe).
    """

    suffix = PurePosixPath(filename).suffix.lower()
    file_type = EXTENSION_TO_FILE_TYPE.get(suffix)
    if file_type is None:
        supported = ", ".join(sorted(EXTENSION_TO_FILE_TYPE))
        raise UnsupportedFileTypeError(
            f"Unsupported file extension '{suffix or '(none)'}'. Supported types: {supported}"
        )
    return file_type


async def read_upload_within_limit(upload: UploadFile, max_bytes: int) -> bytes:
    """Read an UploadFile into memory in bounded chunks.

    Rejects the file as soon as it exceeds `max_bytes` instead of first
    buffering an arbitrarily large payload and checking afterwards — this
    keeps memory use bounded regardless of what a client sends.
    """

    chunks: list[bytes] = []
    total = 0

    while True:
        chunk = await upload.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise FileTooLargeError(
                f"File exceeds the maximum allowed size of {max_bytes // (1024 * 1024)} MB"
            )
        chunks.append(chunk)

    if total == 0:
        raise EmptyFileError("Uploaded file is empty")

    return b"".join(chunks)


def looks_like_xlsx(content: bytes) -> bool:
    """Check the zip magic bytes so a mislabeled .xlsx extension is caught early."""

    return content[:4] == XLSX_MAGIC_BYTES
