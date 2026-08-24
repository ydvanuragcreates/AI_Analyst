"""Pydantic schemas for the Phase 9 v1 (job-based) analysis API."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

AnalysisStatusLiteral = Literal["queued", "running", "waiting_for_approval", "completed", "failed", "cancelled"]
AnalysisModeLiteral = Literal["qa", "deep"]


class CreateAnalysisRequest(BaseModel):
    dataset_id: uuid.UUID
    question: str = Field(
        default="",
        max_length=20_000,
        description="Hard ceiling; Settings.max_question_length is the real, configurable limit enforced in the route. "
        "Not required when mode='deep' -- an autonomous deep analysis does not need a specific question (section 44).",
    )
    mode: AnalysisModeLiteral = Field(
        default="qa",
        description="'qa' (default): answer `question` via the Phase 8 multi-agent Q&A graph, unchanged since Phase 8. "
        "'deep': autonomously profile, investigate, and report on the dataset via the Phase 10 deep-analysis graph -- "
        "`question` is optional in this mode and, if given, only narrows what the analysis focuses on.",
    )

    @model_validator(mode="after")
    def _question_required_for_qa(self) -> "CreateAnalysisRequest":
        if self.mode == "qa" and not self.question.strip():
            raise ValueError("question is required when mode='qa'")
        return self


class AnalysisJobResponse(BaseModel):
    """Response body for POST /api/v1/analysis: 'I queued it,' nothing more."""

    analysis_id: uuid.UUID
    status: AnalysisStatusLiteral


class AnalysisStatusV1Response(BaseModel):
    """Response body for GET /api/v1/analysis/{id} when only status/progress
    matter to the caller (also embedded in AnalysisDetailResponse)."""

    analysis_id: uuid.UUID
    status: AnalysisStatusLiteral
    progress: int = Field(ge=0, le=100)
    error: str | None = None


class AnalysisDetailResponse(BaseModel):
    """Full detail for GET /api/v1/analysis/{id} -- deliberately excludes
    internal agent state (plan, executed SQL, review, retries): only what
    a caller asking 'what did I get' actually needs."""

    analysis_id: uuid.UUID
    dataset_id: uuid.UUID
    question: str
    mode: AnalysisModeLiteral
    status: AnalysisStatusLiteral
    progress: int
    answer: str | None
    confidence: float | None
    error: str | None = Field(
        default=None,
        description="Why this analysis failed, when status='failed' -- already stored on AnalysisJob.error "
        "(see workers/analysis_worker.py's _mark_failed) but previously only surfaced via the cancel/approve "
        "responses, not this primary detail endpoint.",
    )
    token_usage: dict[str, int] | None
    created_at: datetime
    completed_at: datetime | None


class AnalysisHistoryResponse(BaseModel):
    analyses: list[AnalysisDetailResponse]
    total: int
    page: int
    page_size: int


class ApproveV1Request(BaseModel):
    approved: bool
    feedback: str | None = Field(default=None, max_length=5000)
