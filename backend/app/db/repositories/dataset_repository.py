"""Database access for Dataset -- ownership-scoped lookups only.

`get_owned` returns None for both "no such dataset" and "exists but a
different user owns it" -- deliberately the same return value for both,
so the service layer maps either case to the same 404
(DatasetNotFoundError) and never leaks which one it was.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import Dataset


class DatasetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, id: uuid.UUID, user_id: uuid.UUID, name: str, file_name: str, file_type: str,
        file_size: int, file_hash: str, version: int = 1,
    ) -> Dataset:
        dataset = Dataset(
            id=id, user_id=user_id, name=name, file_name=file_name,
            file_type=file_type, file_size=file_size, file_hash=file_hash, version=version,
        )
        self._session.add(dataset)
        await self._session.flush()
        return dataset

    async def get_owned(self, dataset_id: uuid.UUID, user_id: uuid.UUID) -> Dataset | None:
        result = await self._session.execute(
            select(Dataset).where(Dataset.id == dataset_id, Dataset.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_latest_version_by_filename_owned(self, file_name: str, user_id: uuid.UUID) -> Dataset | None:
        """The 'basic dataset versioning' hook: re-uploading a file with
        the same name, for the same user, is treated as a new VERSION of
        the same logical dataset (not an unrelated duplicate) -- see
        api/routes/datasets_v1.py::upload_dataset_v1."""

        result = await self._session.execute(
            select(Dataset)
            .where(Dataset.file_name == file_name, Dataset.user_id == user_id)
            .order_by(Dataset.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[Dataset]:
        result = await self._session.execute(
            select(Dataset).where(Dataset.user_id == user_id).order_by(Dataset.created_at.desc())
        )
        return list(result.scalars().all())

    async def delete(self, dataset: Dataset) -> None:
        await self._session.delete(dataset)
        await self._session.flush()
