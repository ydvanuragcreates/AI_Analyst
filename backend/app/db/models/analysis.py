"""The Analysis model -- one row per analytical question asked through
the Phase 9 v1 (job-based) API. Distinct from AnalysisJob (see
analysis_job.py): Analysis is the durable record of the QUESTION and its
RESULT; AnalysisJob is the ephemeral record of the BACKGROUND EXECUTION
that produced it (status/progress/retries/error). Splitting them is what
lets an Analysis be retried under a fresh AnalysisJob without losing its
own identity/thread_id.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, GUID, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.analysis_job import AnalysisJob
    from app.db.models.analysis_message import AnalysisMessage
    from app.db.models.dataset import Dataset
    from app.db.models.deep_analysis_result import DeepAnalysisResult
    from app.db.models.user import User


class AnalysisStatus:
    """Plain string constants (not a DB enum type -- SQLite/Postgres
    portability, same reasoning as GUID) matching Settings/API docs."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisMode:
    """Phase 10 addition: which graph a given Analysis runs through.
    'qa' (the default) is every Phase 1-9 behavior, completely unchanged
    -- the Phase 8 multi-agent Q&A graph, answering `question` directly.
    'deep' routes to the Phase 10 autonomous deep-analysis graph instead
    (agent/deep_analysis/graph.py), whose rich output lands in a
    DeepAnalysisResult row rather than just `answer`/`confidence`."""

    QA = "qa"
    DEEP = "deep"


class Analysis(Base, TimestampMixin):
    __tablename__ = "analyses"
    __table_args__ = (
        Index("ix_analyses_user_id_created_at", "user_id", "created_at"),
        # Production-readiness audit finding: without this, two
        # concurrent requests carrying the SAME Idempotency-Key (same
        # user) could both pass the application-level "does this key
        # already exist?" check before either had committed, and both
        # create a row -- the DB is what actually makes "same key + same
        # user" impossible to duplicate, not the check-then-insert in
        # api/routes/analysis_jobs.py (which is still the right place to
        # turn the resulting IntegrityError into a clean response). NULL
        # idempotency_key values are never considered equal to each
        # other by a unique constraint (true in both Postgres and
        # SQLite), so requests without a key are correctly never
        # deduplicated by this.
        UniqueConstraint("user_id", "idempotency_key", name="uq_analyses_user_id_idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("datasets.id"), index=True, nullable=False)
    dataset_version: Mapped[int] = mapped_column(
        default=1, doc="Dataset.version at the time this analysis ran -- keeps the record reproducible"
    )

    thread_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=AnalysisStatus.QUEUED, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), default=AnalysisMode.QA, nullable=False)

    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    client_key: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True,
        doc="Hashed caller IP (core/client_ip.py) -- per-visitor daily limits; never the raw IP",
    )

    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="analyses")
    dataset: Mapped["Dataset"] = relationship(back_populates="analyses")
    messages: Mapped[list["AnalysisMessage"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", order_by="AnalysisMessage.created_at"
    )
    job: Mapped["AnalysisJob | None"] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", uselist=False
    )
    deep_result: Mapped["DeepAnalysisResult | None"] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", uselist=False
    )
