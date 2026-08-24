"""
Pydantic schemas for the dataset ingestion API.

These are the typed contracts between DatasetService and the outside
world: FastAPI uses them for request/response validation and OpenAPI docs,
and the service builds them directly instead of passing raw dicts around.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, RootModel


class DatasetColumn(BaseModel):
    """Metadata about a single column in an uploaded dataset."""

    name: str
    data_type: str = Field(description="One of: string, integer, float, boolean, datetime, object")
    nullable: bool


class DatasetPreview(RootModel[list[dict[str, Any]]]):
    """A bounded preview of dataset rows (see DATASET_PREVIEW_ROWS).

    Modeled as a RootModel rather than a wrapper object so it serializes
    as a plain JSON array — matching the flat "preview": [...] shape —
    while still being a named, typed schema instead of a bare dict.
    """


class DatasetMetadata(BaseModel):
    """Full metadata describing an uploaded dataset."""

    dataset_id: str
    filename: str
    file_type: str
    file_size: int
    row_count: int
    column_count: int
    columns: list[DatasetColumn]
    missing_values: dict[str, int]
    preview: DatasetPreview
    uploaded_at: datetime


# Same shape as DatasetMetadata today; kept as a distinct name so the
# upload endpoint's contract can diverge later without touching
# DatasetMetadata (e.g. GET /datasets/{id} vs POST /upload responses).
DatasetUploadResponse = DatasetMetadata


class DatasetListResponse(BaseModel):
    """Response body for GET /api/datasets."""

    datasets: list[DatasetMetadata]
    count: int


class DatasetDeleteResponse(BaseModel):
    """Response body for DELETE /api/datasets/{dataset_id}."""

    dataset_id: str
    deleted: bool = True
