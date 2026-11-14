"""Rebuild local dataset copies from Postgres after the disk was wiped.

Run once at container startup, BEFORE the API and worker start:

    python -m app.scripts.restore_datasets

For hosts with an ephemeral filesystem (see models/dataset_file.py): every
dataset with stored bytes whose local copy is missing gets its file,
DuckDB table and metadata.json recreated. Datasets already present are
skipped, so running it on a host with a persistent disk is a cheap no-op.
First applies Settings.dataset_retention_days (see services/retention.py).
Datasets uploaded before dataset_files existed have no stored bytes and
cannot be restored; they are reported, not treated as an error.

Never fails startup: a dataset that can't be restored is logged and
skipped, so one bad file can't keep the whole app down.
"""

import asyncio
import logging

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models.dataset import Dataset
from app.db.repositories.dataset_repository import DatasetRepository
from app.db.session import get_engine, get_session_factory
from app.services.dataset_service import DatasetService, get_dataset_service
from app.services.exceptions import DatasetNotFoundError
from app.services.retention import purge_expired_datasets

logger = logging.getLogger("restore_datasets")


async def restore_all(dataset_service: DatasetService) -> dict[str, int]:
    counts = {"restored": 0, "already_present": 0, "failed": 0, "no_stored_file": 0}

    async with get_session_factory()() as session:
        rows = await DatasetRepository(session).list_all_with_files()
        total = (await session.execute(select(func.count()).select_from(Dataset))).scalar_one()
    counts["no_stored_file"] = total - len(rows)

    for dataset, content in rows:
        try:
            restored = await asyncio.to_thread(
                dataset_service.restore_dataset,
                dataset_id=str(dataset.id),
                filename=dataset.file_name,
                file_type=dataset.file_type,
                content=content,
                uploaded_at=dataset.created_at,
            )
        except Exception:  # noqa: BLE001 -- skip one bad dataset, keep restoring the rest
            logger.exception("Could not restore dataset %s (%s)", dataset.id, dataset.file_name)
            counts["failed"] += 1
            continue
        counts["restored" if restored else "already_present"] += 1

    return counts


async def purge_expired(dataset_service: DatasetService) -> list:
    """Apply Settings.dataset_retention_days (services/retention.py), then drop
    any local copies of the deleted datasets (present on persistent disks)."""

    async with get_session_factory()() as session:
        deleted = await purge_expired_datasets(session, get_settings().dataset_retention_days)
    for dataset_id in deleted:
        try:
            dataset_service.delete_dataset(str(dataset_id))
        except DatasetNotFoundError:
            pass  # not on this disk (e.g. wiped on restart) -- nothing to remove
    return deleted


async def _main() -> None:
    if not get_settings().database_url:
        logger.info("DATABASE_URL not set; nothing to restore")
        return
    dataset_service = get_dataset_service()
    try:
        await purge_expired(dataset_service)
    except Exception:  # noqa: BLE001 -- never block startup
        logger.exception("Dataset retention cleanup failed; continuing")
    try:
        counts = await restore_all(dataset_service)
        logger.info("Dataset restore finished: %s", counts)
    except Exception:  # noqa: BLE001 -- never block startup
        logger.exception("Dataset restore failed; starting anyway")
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_main())
