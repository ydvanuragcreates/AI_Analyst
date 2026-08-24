"""The Dataset model -- Postgres-side ownership/versioning metadata for a
dataset whose actual file + DuckDB table are still managed exactly as in
Phase 2/3 (DatasetService/DuckDBService, unchanged). `id` here is the
SAME string passed to those services as `dataset_id` -- one identifier,
two systems each tracking what they're responsible for (see
services/analysis_service.py for how a v1 dataset upload registers both).
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, GUID, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.analysis import Analysis
    from app.db.models.user import User


class Dataset(Base, TimestampMixin):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    file_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="sha256 hex digest of the file content, used in cache keys"
    )

    user: Mapped["User"] = relationship(back_populates="datasets")
    analyses: Mapped[list["Analysis"]] = relationship(back_populates="dataset")
