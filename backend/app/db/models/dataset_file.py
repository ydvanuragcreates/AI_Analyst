"""The raw bytes of an uploaded dataset file, kept in Postgres.

DatasetService stores each upload on local disk (original file +
metadata.json + a DuckDB table). On hosts with an ephemeral filesystem
(e.g. a free container platform that wipes its disk on every restart)
that local copy disappears while the `datasets` row survives. Keeping the
original bytes here lets app/scripts/restore_datasets.py rebuild the local
copy at startup (see DatasetService.restore_dataset).

A separate table (rather than a column on Dataset) so listing/reading
datasets never loads file contents unless restore actually needs them.
"""

import uuid

from sqlalchemy import ForeignKey, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, GUID, TimestampMixin


class DatasetFile(Base, TimestampMixin):
    __tablename__ = "dataset_files"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
