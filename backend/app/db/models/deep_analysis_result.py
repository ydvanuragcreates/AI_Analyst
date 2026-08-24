"""The DeepAnalysisResult model -- one row per Phase 10 autonomous
deep-analysis run (Analysis.mode == 'deep'), one-to-one with Analysis.

Kept as its OWN table rather than new columns on Analysis: the ordinary
Q&A path (Analysis.mode == 'qa', the overwhelming majority of rows) has
no use for profile/insights/trends/anomalies/correlations/segments/
report/visualizations/trace/budget -- adding all of that to every
Analysis row would bloat the table Phase 1-9 already built and tested
for a much smaller record. A dataset's rich analytical artifacts belong
in their own table for the same reason AnalysisJob is split from
Analysis (db/models/analysis_job.py): a different concern, populated by
a different part of the pipeline, with its own lifecycle.

Every JSON column here stores the `.model_dump(mode="json")` of the
corresponding Pydantic list/object from schemas/deep_analysis.py -- the
API layer re-validates back into those same schemas when reading, so the
stored shape and the API response shape never drift apart silently.
"""

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, GUID, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.analysis import Analysis


class DeepAnalysisResult(Base, TimestampMixin):
    __tablename__ = "deep_analysis_results"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    analysis_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("analyses.id"), unique=True, index=True, nullable=False)

    status: Mapped[str] = mapped_column(default="completed", nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    profile: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    data_quality: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    insights: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    trends: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    anomalies: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    correlations: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    segments: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    hypothesis_results: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    recommendations: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    visualizations: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    limitations: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    trace: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    budget: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    analysis: Mapped["Analysis"] = relationship(back_populates="deep_result")
