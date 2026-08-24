"""Database access for DeepAnalysisResult.

Ownership is never checked here directly -- callers reach a
DeepAnalysisResult only via an already-ownership-checked Analysis (see
AnalysisRepository.get_owned), same as AnalysisJob/AnalysisMessage.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.deep_analysis_result import DeepAnalysisResult


class DeepAnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, analysis_id: uuid.UUID, fields: dict[str, Any]) -> DeepAnalysisResult:
        result = DeepAnalysisResult(analysis_id=analysis_id, **fields)
        self._session.add(result)
        await self._session.flush()
        return result

    async def get_by_analysis_id(self, analysis_id: uuid.UUID) -> DeepAnalysisResult | None:
        result = await self._session.execute(
            select(DeepAnalysisResult).where(DeepAnalysisResult.analysis_id == analysis_id)
        )
        return result.scalar_one_or_none()
