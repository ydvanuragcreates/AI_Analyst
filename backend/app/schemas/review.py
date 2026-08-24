"""
Review/quality-control schema (Phase 8).

Kept in its own file per the project's structure diagram: the Reviewer's
output is a distinct concern from the analytical schemas in
schemas/analysis.py, and it's small enough that colocating it there would
blur "what the analysis pipeline produces" with "how it gets checked."
"""

from pydantic import BaseModel, Field


class ReviewResult(BaseModel):
    """Structured output of the Reviewer agent (agent/reviewer.py).

    A judgment about the Data Analyst's AnalyticalFindings, checked
    against the actual query results the SQL Analyst produced -- not a
    second opinion generated from nothing. See agent/reviewer.py for
    exactly what context the Reviewer is given to check against.
    """

    approved: bool = Field(description="Whether the findings are accurate, supported, and address the question")
    issues: list[str] = Field(
        default_factory=list, description="Specific problems found, e.g. 'growth percentage does not match query results'"
    )
    corrections: list[str] = Field(
        default_factory=list, description="Specific guidance for what the next attempt should fix"
    )
    confidence: float = Field(
        description="The reviewer's own confidence in this judgment (0-1) -- also what "
        "Settings.human_approval_confidence_threshold is checked against for human-in-the-loop escalation",
        ge=0.0,
        le=1.0,
    )
