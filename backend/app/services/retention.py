"""Delete old datasets (and everything that depends on them).

For a public demo on free-tier storage (Neon: 0.5GB): every upload keeps
its original bytes in Postgres (models/dataset_file.py), so uploads would
otherwise pile up forever. Settings.dataset_retention_days > 0 turns this
on; it runs at startup from scripts/restore_datasets.py, before anything
is restored -- which on a free host that sleeps when idle means "often".

A dataset is removed together with its analyses, because the normal
delete route refuses to drop a dataset that analyses still reference
(historical analyses must stay reproducible -- see datasets_v1.py). That
rule protects users from accidental deletes; expiry is the deliberate,
documented exception.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.analysis import Analysis
from app.db.models.analysis_job import AnalysisJob
from app.db.models.analysis_message import AnalysisMessage
from app.db.models.dataset import Dataset
from app.db.models.dataset_file import DatasetFile
from app.db.models.deep_analysis_result import DeepAnalysisResult

logger = logging.getLogger(__name__)

# LangGraph's Postgres checkpointer tables (keyed by the analysis's thread_id).
_CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


async def purge_expired_datasets(
    session: AsyncSession, retention_days: int, *, now: datetime | None = None
) -> list[uuid.UUID]:
    """Delete datasets created more than `retention_days` ago, with their
    analyses, stored files and agent checkpoints. Commits; returns the
    deleted dataset ids (callers remove local copies). No-op if <= 0."""

    if retention_days <= 0:
        return []

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    dataset_ids = list((await session.execute(select(Dataset.id).where(Dataset.created_at < cutoff))).scalars())
    if not dataset_ids:
        return []

    analyses = (
        await session.execute(select(Analysis.id, Analysis.thread_id).where(Analysis.dataset_id.in_(dataset_ids)))
    ).all()
    analysis_ids = [row.id for row in analyses]
    thread_ids = [row.thread_id for row in analyses]

    if analysis_ids:
        for model in (DeepAnalysisResult, AnalysisMessage, AnalysisJob):
            await session.execute(delete(model).where(model.analysis_id.in_(analysis_ids)))
        if thread_ids and session.bind.dialect.name == "postgresql":
            for table in _CHECKPOINT_TABLES:
                exists = (await session.execute(text("SELECT to_regclass(:t)"), {"t": table})).scalar()
                if exists:
                    await session.execute(
                        text(f"DELETE FROM {table} WHERE thread_id = ANY(:ids)"), {"ids": thread_ids}
                    )
        await session.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))

    await session.execute(delete(DatasetFile).where(DatasetFile.dataset_id.in_(dataset_ids)))
    await session.execute(delete(Dataset).where(Dataset.id.in_(dataset_ids)))
    await session.commit()

    logger.info(
        "Retention: deleted %d dataset(s) older than %d days and %d analysis/analyses",
        len(dataset_ids), retention_days, len(analysis_ids),
    )
    return dataset_ids
