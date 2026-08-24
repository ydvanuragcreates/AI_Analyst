"""
Unit tests for the low-level file helpers in app/utils/file_utils.py.

The oversized-file check is tested directly here against a small max_bytes
limit rather than via the HTTP API with a genuinely huge upload — same
code path, without needing a real 50MB+ payload in the test suite.
"""

import io

import pytest
from fastapi import UploadFile

from app.services.exceptions import EmptyFileError, FileTooLargeError, UnsupportedFileTypeError
from app.utils.file_utils import (
    looks_like_xlsx,
    read_upload_within_limit,
    resolve_file_type,
    sanitize_filename,
)


def test_sanitize_filename_strips_directory_components() -> None:
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\windows\\evil.csv") == "evil.csv"
    assert sanitize_filename("sales.csv") == "sales.csv"


def test_sanitize_filename_handles_missing_name() -> None:
    assert sanitize_filename(None) == "upload"
    assert sanitize_filename("") == "upload"


def test_resolve_file_type_accepts_supported_extensions() -> None:
    assert resolve_file_type("sales.csv") == "csv"
    assert resolve_file_type("sales.XLSX") == "xlsx"
    assert resolve_file_type("sales.json") == "json"


def test_resolve_file_type_rejects_unsupported_extension() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        resolve_file_type("sales.exe")


def test_looks_like_xlsx_checks_zip_signature() -> None:
    assert looks_like_xlsx(b"PK\x03\x04rest-of-zip-content") is True
    assert looks_like_xlsx(b"not a zip file at all") is False


@pytest.mark.anyio
async def test_read_upload_within_limit_rejects_oversized_file() -> None:
    upload = UploadFile(filename="big.csv", file=io.BytesIO(b"x" * 100))

    with pytest.raises(FileTooLargeError):
        await read_upload_within_limit(upload, max_bytes=10)


@pytest.mark.anyio
async def test_read_upload_within_limit_rejects_empty_file() -> None:
    upload = UploadFile(filename="empty.csv", file=io.BytesIO(b""))

    with pytest.raises(EmptyFileError):
        await read_upload_within_limit(upload, max_bytes=1024)


@pytest.mark.anyio
async def test_read_upload_within_limit_accepts_file_within_limit() -> None:
    upload = UploadFile(filename="small.csv", file=io.BytesIO(b"a,b\n1,2\n"))

    content = await read_upload_within_limit(upload, max_bytes=1024)

    assert content == b"a,b\n1,2\n"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
