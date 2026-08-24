"""AnalysisMessage -- the question/answer transcript for an Analysis.

Kept as its own table (rather than two text columns on Analysis) so a
future multi-turn analysis thread can append more than one exchange
without a schema change -- Phase 9 only ever writes exactly one "user"
and one "assistant" row per Analysis, but the model doesn't assume that.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, GUID, utcnow

if TYPE_CHECKING:
    from app.db.models.analysis import Analysis


class AnalysisMessage(Base):
    __tablename__ = "analysis_messages"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    analysis_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("analyses.id"), index=True, nullable=False)

    role: Mapped[str] = mapped_column(String(16), nullable=False, doc="'user' or 'assistant'")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    analysis: Mapped["Analysis"] = relationship(back_populates="messages")
