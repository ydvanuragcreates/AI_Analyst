"""
Phase 9 production-readiness audit -- Celery retry / duplicate-execution
behavior (section 7).

Honest limitation, confirmed empirically while writing these tests:
Celery's `task_always_eager` mode (used throughout this environment --
see workers/celery_app.py, automatic whenever REDIS_URL is unset) does
NOT automatically re-invoke a task after `self.retry()` raises
`celery.exceptions.Retry`. In REAL (non-eager) Celery, a worker's own
task-execution harness catches that exception and re-queues the task via
the broker for a later delivery; eager mode has no broker to re-queue
through, so `Retry` simply propagates to whoever called `.delay()`
(handled without crashing the request -- see
api/routes/analysis_jobs.py::_delay_tolerating_eager_retry -- but it does
NOT retry on its own). This is a genuine constraint of testing without a
real Celery worker + broker, not a bug in this project's retry logic;
these tests are written to prove what's ACTUALLY true in this
environment (the task raises the right signal and leaves the DB in a
recoverable state) plus, separately, what a redelivery-driven retry
sequence produces when driven explicitly via `task.apply(retries=N)`
(Celery's own documented mechanism for simulating "this is redelivery
attempt N" without a real broker).
"""

import uuid

import openai
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.analysis import AnalyticalFindings, AnalysisResponse, GeneratedSQL, ResultValidation, SupervisorDecision
from app.schemas.review import ReviewResult
from tests.conftest import FakeStructuredModel

pytestmark = pytest.mark.usefixtures("sqlite_db")


def _supervisor_decision(**overrides) -> SupervisorDecision:
    defaults = dict(complexity="simple", requires_planning=False, requires_data_analysis=True, is_answerable=True, reasoning="x")
    defaults.update(overrides)
    return SupervisorDecision(**defaults)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def registered_user(client: TestClient):
    email = f"{uuid.uuid4().hex}@example.com"
    response = client.post("/api/v1/auth/register", json={"email": email, "password": "supersecret123"})
    token = response.json()["access_token"]
    return {"headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture
def dataset(client: TestClient, registered_user):
    with open("tests/fixtures/sales.csv", "rb") as fh:
        response = client.post("/api/v1/datasets", files={"file": ("sales.csv", fh, "text/csv")}, headers=registered_user["headers"])
    return response.json()


def _patch_fake_graph(monkeypatch, worker_module) -> None:
    real_build = worker_module.build_multi_agent_graph
    sql_model = FakeStructuredModel(GeneratedSQL(sql="SELECT region, SUM(sales) AS total FROM dataset GROUP BY region"))
    result_model = FakeStructuredModel(ResultValidation(is_sufficient=True, reason="ok", confidence=0.9))
    data_analyst_model = FakeStructuredModel(
        AnalyticalFindings(answer="Mumbai leads.", key_findings=["x"], metrics=[], anomalies=[], confidence=0.9)
    )
    reviewer_model = FakeStructuredModel(ReviewResult(approved=True, issues=[], corrections=[], confidence=0.9))
    final_model = FakeStructuredModel(
        AnalysisResponse(answer="Mumbai leads.", key_findings=["x"], metrics=[], sql_used=[], confidence=0.9, chart=None, follow_up_suggestions=[])
    )
    supervisor_model = FakeStructuredModel(_supervisor_decision())

    def fake_build(*, checkpointer):
        return real_build(
            checkpointer=checkpointer, supervisor_model=supervisor_model, sql_model=sql_model,
            result_model=result_model, data_analyst_model=data_analyst_model,
            reviewer_model=reviewer_model, final_model=final_model,
        )

    monkeypatch.setattr(worker_module, "build_multi_agent_graph", fake_build)


def test_transient_failure_leaves_a_recoverable_state_not_a_crash(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    """task executes -> failure: the create request must not 500 (see
    _delay_tolerating_eager_retry), and the analysis must be left
    `running` (not silently `failed`, not falsely `completed`) -- exactly
    the state a real redelivery would find it in and correctly resume
    from."""

    import app.workers.analysis_worker as worker
    from app.db.models.analysis import AnalysisStatus

    async def always_fails(analysis_id: str):
        raise openai.APIConnectionError(request=None)

    monkeypatch.setattr(worker, "_run_analysis_job", always_fails)
    _patch_fake_graph(monkeypatch, worker)

    response = client.post(
        "/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"},
        headers=registered_user["headers"],
    )
    assert response.status_code == 202, "a retryable background failure must not crash the request that created the job"
    analysis_id = response.json()["analysis_id"]

    detail = client.get(f"/api/v1/analysis/{analysis_id}", headers=registered_user["headers"]).json()
    assert detail["status"] in (AnalysisStatus.QUEUED, AnalysisStatus.RUNNING), (
        f"expected a recoverable non-terminal status, got {detail['status']!r} "
        "(overwritten to failed/completed would be the actual bug this guards against)"
    )


def test_redelivery_driven_retry_then_success_produces_no_duplicate_message(
    client: TestClient, registered_user, dataset, monkeypatch
) -> None:
    """Simulates what a REAL Celery worker + broker would do after
    `self.retry()`: redeliver the task (here, via `task.apply(retries=1)`,
    Celery's own documented way to simulate 'this is redelivery attempt
    N' without a real broker -- see the module docstring for why eager
    mode can't do this automatically). First delivery fails; the
    redelivery succeeds; the end state is exactly one completed analysis
    with exactly one assistant message, never two."""

    import app.workers.analysis_worker as worker

    call_count = {"n": 0}
    real_run = worker._run_analysis_job

    async def flaky_run(analysis_id: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise openai.APIConnectionError(request=None)
        await real_run(analysis_id)

    monkeypatch.setattr(worker, "_run_analysis_job", flaky_run)
    _patch_fake_graph(monkeypatch, worker)

    created = client.post(
        "/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"},
        headers=registered_user["headers"],
    )
    analysis_id = created.json()["analysis_id"]
    assert call_count["n"] == 1  # the first (failing) delivery

    # The redelivery a real broker would perform after self.retry() --
    # `retries=1` tells Celery this is attempt #2, matching what a real
    # redelivered message's headers would say.
    worker.run_analysis_job.apply(args=(analysis_id,), kwargs={"request_id": None}, retries=1)
    assert call_count["n"] == 2

    detail = client.get(f"/api/v1/analysis/{analysis_id}", headers=registered_user["headers"]).json()
    assert detail["status"] == "completed"
    assert detail["answer"] == "Mumbai leads."

    import asyncio

    from sqlalchemy import select

    from app.db.models.analysis_message import AnalysisMessage
    from app.db.session import get_session_factory

    async def _count_messages():
        async with get_session_factory()() as session:
            result = await session.execute(
                select(AnalysisMessage).where(AnalysisMessage.analysis_id == uuid.UUID(analysis_id), AnalysisMessage.role == "assistant")
            )
            return len(result.scalars().all())

    assert asyncio.run(_count_messages()) == 1, "a retried-then-succeeded job must produce exactly one assistant message, not one per attempt"


def test_max_retries_exceeded_marks_analysis_failed(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    """When retries are exhausted (simulated via `retries=job_max_retries`,
    the same mechanism used above), self.retry() raises
    MaxRetriesExceededError instead of Retry -- run_analysis_job must
    catch that specifically and mark the analysis failed, not leave it
    stuck `running` forever."""

    import app.workers.analysis_worker as worker
    from app.core.config import get_settings
    from app.db.models.analysis import AnalysisStatus

    async def always_fails(analysis_id: str):
        raise openai.APIConnectionError(request=None)

    monkeypatch.setattr(worker, "_run_analysis_job", always_fails)
    _patch_fake_graph(monkeypatch, worker)

    created = client.post(
        "/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"},
        headers=registered_user["headers"],
    )
    analysis_id = created.json()["analysis_id"]

    max_retries = get_settings().job_max_retries
    worker.run_analysis_job.apply(args=(analysis_id,), kwargs={"request_id": None}, retries=max_retries)

    detail = client.get(f"/api/v1/analysis/{analysis_id}", headers=registered_user["headers"]).json()
    assert detail["status"] == AnalysisStatus.FAILED


def test_redelivered_task_on_already_completed_analysis_is_skipped(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    """Simulates 'worker crashes after DB update, then redelivery
    retries the task': by the time run_analysis_job runs (again), the
    Analysis is already `completed` in the DB. The duplicate-execution
    guard must skip re-running it, not redo the whole analysis."""

    import app.workers.analysis_worker as worker

    graph_call_count = {"n": 0}
    real_build = worker.build_multi_agent_graph
    sql_model = FakeStructuredModel(GeneratedSQL(sql="SELECT region, SUM(sales) AS total FROM dataset GROUP BY region"))
    result_model = FakeStructuredModel(ResultValidation(is_sufficient=True, reason="ok", confidence=0.9))
    data_analyst_model = FakeStructuredModel(
        AnalyticalFindings(answer="Mumbai leads.", key_findings=["x"], metrics=[], anomalies=[], confidence=0.9)
    )
    reviewer_model = FakeStructuredModel(ReviewResult(approved=True, issues=[], corrections=[], confidence=0.9))
    final_model = FakeStructuredModel(
        AnalysisResponse(answer="Mumbai leads.", key_findings=["x"], metrics=[], sql_used=[], confidence=0.9, chart=None, follow_up_suggestions=[])
    )
    supervisor_model = FakeStructuredModel(_supervisor_decision())

    def fake_build(*, checkpointer):
        graph_call_count["n"] += 1
        return real_build(
            checkpointer=checkpointer, supervisor_model=supervisor_model, sql_model=sql_model,
            result_model=result_model, data_analyst_model=data_analyst_model,
            reviewer_model=reviewer_model, final_model=final_model,
        )

    monkeypatch.setattr(worker, "build_multi_agent_graph", fake_build)

    created = client.post(
        "/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"},
        headers=registered_user["headers"],
    )
    analysis_id = created.json()["analysis_id"]
    assert created.json()["status"] == "completed"
    calls_after_first_run = graph_call_count["n"]
    assert calls_after_first_run == 1

    # Simulate a redelivered task for the SAME analysis (e.g. the broker
    # never received the ack before the worker died, even though the
    # work had already finished).
    worker.run_analysis_job.apply(args=(analysis_id,), kwargs={"request_id": None})

    assert graph_call_count["n"] == calls_after_first_run, (
        "the duplicate-execution guard should have skipped re-running the graph entirely"
    )
