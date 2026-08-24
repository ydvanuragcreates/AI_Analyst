"""Database access for Analysis / AnalysisJob / AnalysisMessage.

Ownership-scoped lookups follow the same "None means not found OR not
yours, indistinguishable" rule as DatasetRepository.
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.analysis import Analysis, AnalysisMode, AnalysisStatus
from app.db.models.analysis_job import AnalysisJob, JobStatus
from app.db.models.analysis_message import AnalysisMessage


class AnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        dataset_id: uuid.UUID,
        dataset_version: int,
        thread_id: str,
        question: str,
        idempotency_key: str | None,
        mode: str = AnalysisMode.QA,
    ) -> Analysis:
        analysis = Analysis(
            user_id=user_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            thread_id=thread_id,
            question=question,
            status=AnalysisStatus.QUEUED,
            idempotency_key=idempotency_key,
            mode=mode,
        )
        self._session.add(analysis)
        await self._session.flush()

        self._session.add(AnalysisMessage(analysis_id=analysis.id, role="user", content=question))
        self._session.add(AnalysisJob(analysis_id=analysis.id, status=JobStatus.QUEUED, progress=0))
        await self._session.flush()

        # An AsyncSession has no implicit lazy-loading -- a plain
        # relationship access on `analysis` after this point (e.g.
        # `analysis.job.progress` back in the route) would otherwise try
        # to lazy-load synchronously and raise MissingGreenlet. Even
        # assigning `analysis.job = job` directly doesn't avoid this:
        # back_populates bookkeeping reads the current (unloaded) value
        # first. `refresh(..., attribute_names=...)` is the actual
        # async-safe way to populate specific relationships.
        await self._session.refresh(analysis, attribute_names=["job", "messages"])
        return analysis

    async def get_owned(self, analysis_id: uuid.UUID, user_id: uuid.UUID) -> Analysis | None:
        result = await self._session.execute(
            select(Analysis)
            .where(Analysis.id == analysis_id, Analysis.user_id == user_id)
            .options(selectinload(Analysis.job), selectinload(Analysis.messages))
        )
        return result.scalar_one_or_none()

    async def get_by_idempotency_key_owned(self, idempotency_key: str, user_id: uuid.UUID) -> Analysis | None:
        result = await self._session.execute(
            select(Analysis).where(Analysis.idempotency_key == idempotency_key, Analysis.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        dataset_id: uuid.UUID | None = None,
        status: str | None = None,
        mode: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        page: int,
        page_size: int,
    ) -> tuple[list[Analysis], int]:
        conditions = [Analysis.user_id == user_id]
        if dataset_id is not None:
            conditions.append(Analysis.dataset_id == dataset_id)
        if status is not None:
            conditions.append(Analysis.status == status)
        if mode is not None:
            conditions.append(Analysis.mode == mode)
        if created_after is not None:
            conditions.append(Analysis.created_at >= created_after)
        if created_before is not None:
            conditions.append(Analysis.created_at <= created_before)

        count_result = await self._session.execute(select(func.count()).select_from(Analysis).where(*conditions))
        total = count_result.scalar_one()

        result = await self._session.execute(
            select(Analysis)
            .where(*conditions)
            .options(selectinload(Analysis.job))
            .order_by(Analysis.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def get_job_owned(self, analysis_id: uuid.UUID, user_id: uuid.UUID) -> AnalysisJob | None:
        analysis = await self.get_owned(analysis_id, user_id)
        return analysis.job if analysis else None

    async def exists_for_dataset(self, dataset_id: uuid.UUID) -> bool:
        """Used before deleting a Dataset (see api/routes/datasets_v1.py)
        -- checked at the application level rather than relying on the
        `analyses.dataset_id` foreign key to reject the delete, because
        SQLite (used throughout this test suite -- no real Postgres
        server is available here) does not enforce foreign keys unless a
        connection explicitly turns on `PRAGMA foreign_keys=ON`, which
        this project's engine does not; a check that only works because
        Postgres happens to enforce it would leave the SQLite-tested path
        silently unverified."""

        result = await self._session.execute(select(Analysis.id).where(Analysis.dataset_id == dataset_id).limit(1))
        return result.scalar_one_or_none() is not None
