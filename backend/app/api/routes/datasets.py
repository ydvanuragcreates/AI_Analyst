"""
Dataset ingestion endpoints.

Routes stay thin: parse the request, call DatasetService, return its
result. All validation, storage, and inspection logic lives in
DatasetService — see services/dataset_service.py. Errors are not caught
here; DatasetServiceError subclasses propagate to the handler registered
in main.py, which maps them to HTTP status codes in one place.
"""

from fastapi import APIRouter, Depends, File, UploadFile

from app.schemas.dataset import (
    DatasetDeleteResponse,
    DatasetListResponse,
    DatasetMetadata,
    DatasetUploadResponse,
)
from app.services.dataset_service import DatasetService, get_dataset_service

router = APIRouter()


@router.post("/upload", response_model=DatasetUploadResponse, status_code=201)
async def upload_dataset(
    file: UploadFile = File(...),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> DatasetUploadResponse:
    """Validate, store, and inspect an uploaded CSV/XLSX/JSON dataset."""

    return await dataset_service.upload_dataset(file)


@router.get("", response_model=DatasetListResponse)
async def list_datasets(
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> DatasetListResponse:
    """List metadata for every dataset currently stored locally."""

    datasets = dataset_service.list_datasets()
    return DatasetListResponse(datasets=datasets, count=len(datasets))


@router.get("/{dataset_id}", response_model=DatasetMetadata)
async def get_dataset(
    dataset_id: str,
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> DatasetMetadata:
    """Return metadata for one dataset, or 404 if it doesn't exist."""

    return dataset_service.get_dataset(dataset_id)


@router.delete("/{dataset_id}", response_model=DatasetDeleteResponse)
async def delete_dataset(
    dataset_id: str,
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> DatasetDeleteResponse:
    """Delete a dataset's stored file and metadata, or 404 if it doesn't exist."""

    dataset_service.delete_dataset(dataset_id)
    return DatasetDeleteResponse(dataset_id=dataset_id)
