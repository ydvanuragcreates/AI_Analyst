"""Response schemas for the Phase 9 v1 dataset endpoints -- wraps the
existing Phase 2/3 DatasetMetadata with the ownership/versioning fields
that only exist in Postgres (see db/models/dataset.py)."""

import uuid

from pydantic import BaseModel

from app.schemas.dataset import DatasetMetadata


class DatasetV1Response(BaseModel):
    dataset_id: uuid.UUID
    version: int
    metadata: DatasetMetadata


class DatasetV1ListResponse(BaseModel):
    datasets: list[DatasetV1Response]
    count: int
