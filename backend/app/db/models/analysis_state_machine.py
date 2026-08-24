"""
Valid Analysis/AnalysisJob status transitions.

Phase 9 production-readiness audit finding: nothing previously stopped a
status write from going backwards (e.g. a redelivered/duplicate Celery
task re-running an already-`completed` analysis and overwriting it with
`running` again). `ALLOWED_TRANSITIONS` is the single source of truth for
"what's a legal next status," used by the worker (as a duplicate-execution
guard -- see workers/analysis_worker.py) and available to anything else
that transitions an Analysis's status.

    queued -> running | cancelled
    running -> waiting_for_approval | completed | failed | cancelled
    waiting_for_approval -> running | cancelled
    completed, failed, cancelled -> (terminal; nothing, without explicit retry semantics)
"""

from app.db.models.analysis import AnalysisStatus

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    AnalysisStatus.QUEUED: {AnalysisStatus.RUNNING, AnalysisStatus.CANCELLED},
    AnalysisStatus.RUNNING: {
        AnalysisStatus.WAITING_FOR_APPROVAL,
        AnalysisStatus.COMPLETED,
        AnalysisStatus.FAILED,
        AnalysisStatus.CANCELLED,
    },
    AnalysisStatus.WAITING_FOR_APPROVAL: {AnalysisStatus.RUNNING, AnalysisStatus.CANCELLED},
    AnalysisStatus.COMPLETED: set(),
    AnalysisStatus.FAILED: set(),
    AnalysisStatus.CANCELLED: set(),
}

TERMINAL_STATUSES = frozenset({AnalysisStatus.COMPLETED, AnalysisStatus.FAILED, AnalysisStatus.CANCELLED})


def is_valid_transition(current: str, new: str) -> bool:
    """Same status is never "valid to transition to" (that's a no-op, not
    a transition) -- callers that mean "set it regardless" should check
    for that separately; this function is specifically about *changes*."""

    return new in ALLOWED_TRANSITIONS.get(current, set())
