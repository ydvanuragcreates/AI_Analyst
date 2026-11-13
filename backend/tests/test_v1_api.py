"""
Tests for the Phase 9 v1 API: auth, owned/versioned datasets, and the
job-based analysis flow (create -> Celery-eager worker execution ->
status/history/cancel/approve), plus rate limiting, caching, and the
human-in-the-loop interrupt/resume path at the worker level.

Two things there is no local infrastructure for in this environment (no
Docker, no Postgres, no Redis server):
- The DB layer is tested against a real SQLite file (see conftest.py's
  `sqlite_db` fixture) instead of Postgres -- the GUID type (db/base.py)
  is what makes the exact same models/migration work on both.
- Redis-dependent behavior (rate limiting, caching, SSE pub/sub, Celery's
  broker) is tested against fakeredis (`fake_redis` fixture) and Celery's
  `task_always_eager` mode (automatic whenever REDIS_URL is unset -- see
  workers/celery_app.py), which runs a "background" job inline,
  synchronously, in the same test process.

The worker's own graph is monkeypatched to use FakeStructuredModel
throughout (see _patch_worker_graph) -- no real OpenAI calls happen
anywhere in this file.
"""

import uuid

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


def _findings(**overrides) -> AnalyticalFindings:
    defaults = dict(answer="Mumbai leads.", key_findings=["Mumbai: 15000"], metrics=[], anomalies=[], confidence=0.9)
    defaults.update(overrides)
    return AnalyticalFindings(**defaults)


def _review(**overrides) -> ReviewResult:
    defaults = dict(approved=True, issues=[], corrections=[], confidence=0.9)
    defaults.update(overrides)
    return ReviewResult(**defaults)


def _final(**overrides) -> AnalysisResponse:
    defaults = dict(answer="Mumbai leads.", key_findings=["Mumbai: 15000"], metrics=[], sql_used=[], confidence=0.9, chart=None, follow_up_suggestions=[])
    defaults.update(overrides)
    return AnalysisResponse(**defaults)


def _patch_worker_graph(monkeypatch, *, reviewer_model=None, simulate_restart_between_jobs=False):
    """Replace the worker's real (OpenAI-backed) graph construction with
    one built entirely from FakeStructuredModel -- see agent/graph.py's
    build_multi_agent_graph, unchanged; only the *models* passed to it
    are swapped here.

    `simulate_restart_between_jobs=True` hands every job a FRESH
    InMemorySaver, i.e. what a backend restart between the approval
    pause and the resume looks like in local (no-Postgres) mode. Without
    it, the worker's real checkpointer_context() is used: one
    process-wide InMemorySaver, which is what lets a resume find the
    paused thread.
    """

    import app.workers.analysis_worker as worker

    real_build = worker.build_multi_agent_graph
    sql_model = FakeStructuredModel(GeneratedSQL(sql="SELECT region, SUM(sales) AS total FROM dataset GROUP BY region"))
    result_model = FakeStructuredModel(ResultValidation(is_sufficient=True, reason="ok", confidence=0.9))
    data_analyst_model = FakeStructuredModel(_findings())
    reviewer_model = reviewer_model or FakeStructuredModel(_review())
    final_model = FakeStructuredModel(_final())
    supervisor_model = FakeStructuredModel(_supervisor_decision())

    def fake_build(*, checkpointer):
        return real_build(
            checkpointer=checkpointer, supervisor_model=supervisor_model, sql_model=sql_model,
            result_model=result_model, data_analyst_model=data_analyst_model,
            reviewer_model=reviewer_model, final_model=final_model,
        )

    monkeypatch.setattr(worker, "build_multi_agent_graph", fake_build)

    if simulate_restart_between_jobs:
        from contextlib import asynccontextmanager

        from langgraph.checkpoint.memory import InMemorySaver

        @asynccontextmanager
        async def fresh_checkpointer_context(database_url):
            yield InMemorySaver()

        monkeypatch.setattr(worker, "checkpointer_context", fresh_checkpointer_context)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def registered_user():
    """Authentication is removed -- every request is the built-in local
    user, so no headers are needed. Kept as a fixture so tests read the same."""
    return {"headers": {}}


@pytest.fixture
def dataset(client: TestClient, registered_user):
    with open("tests/fixtures/sales.csv", "rb") as fh:
        response = client.post("/api/v1/datasets", files={"file": ("sales.csv", fh, "text/csv")}, headers=registered_user["headers"])
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------------ #
# DATASETS
# ------------------------------------------------------------------ #


def test_upload_dataset_v1_creates_owned_versioned_row(dataset) -> None:
    assert dataset["version"] == 1
    assert dataset["metadata"]["filename"] == "sales.csv"


def test_reupload_same_filename_bumps_version(client: TestClient, registered_user, dataset) -> None:
    with open("tests/fixtures/sales.csv", "rb") as fh:
        response = client.post("/api/v1/datasets", files={"file": ("sales.csv", fh, "text/csv")}, headers=registered_user["headers"])
    assert response.status_code == 201
    assert response.json()["version"] == 2


def test_upload_invalid_file_type_rejected(client: TestClient, registered_user) -> None:
    response = client.post(
        "/api/v1/datasets", files={"file": ("notes.txt", b"hello", "text/plain")}, headers=registered_user["headers"]
    )
    assert response.status_code == 400


def test_upload_oversized_file_rejected(client: TestClient, registered_user) -> None:
    # DatasetService caches Settings.max_file_size_bytes at construction
    # (see services/dataset_service.py), and get_dataset_service() is a
    # process-wide @lru_cache singleton -- shrinking the GLOBAL setting
    # for one test would leak a too-small limit into every later test
    # that uploads a dataset. A dependency_override with a throwaway
    # DatasetService instance (same pattern every other test in this
    # codebase uses to swap a dependency) is scoped correctly instead.
    from app.services.dataset_service import DatasetService, get_dataset_service
    from app.services.duckdb_service import get_duckdb_service
    from app.core.config import get_settings

    tiny_limit_service = DatasetService(get_settings(), get_duckdb_service())
    tiny_limit_service._max_file_size_bytes = 1  # 1 byte -- any real CSV content exceeds this

    app.dependency_overrides[get_dataset_service] = lambda: tiny_limit_service
    try:
        response = client.post(
            "/api/v1/datasets", files={"file": ("sales.csv", b"region,sales\nDelhi,100\n", "text/csv")}, headers=registered_user["headers"]
        )
    finally:
        app.dependency_overrides.pop(get_dataset_service, None)

    assert response.status_code == 400


def test_get_dataset_v1_returns_owned_metadata(client: TestClient, registered_user, dataset) -> None:
    response = client.get(f"/api/v1/datasets/{dataset['dataset_id']}", headers=registered_user["headers"])
    assert response.status_code == 200
    assert response.json()["dataset_id"] == dataset["dataset_id"]


def test_delete_dataset_with_existing_analyses_is_rejected(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    """Production-readiness audit fix: deleting the FILE/DuckDB table
    before discovering the Postgres delete would fail (existing Analysis
    rows reference it) would leave a Dataset row pointing at a file that
    no longer exists. Checked -- and rejected -- before either delete
    starts."""

    _patch_worker_graph(monkeypatch)
    client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"}, headers=registered_user["headers"])

    response = client.delete(f"/api/v1/datasets/{dataset['dataset_id']}", headers=registered_user["headers"])
    assert response.status_code == 422

    # And the dataset must still be fully intact -- neither the file nor
    # the Postgres row was touched.
    still_there = client.get(f"/api/v1/datasets/{dataset['dataset_id']}", headers=registered_user["headers"])
    assert still_there.status_code == 200


def test_upload_rejects_dataset_exceeding_max_rows(client: TestClient, registered_user, monkeypatch) -> None:
    from app.services.dataset_service import DatasetService, get_dataset_service
    from app.services.duckdb_service import get_duckdb_service
    from app.core.config import get_settings
    from app.main import app

    tiny_row_limit_service = DatasetService(get_settings(), get_duckdb_service())
    tiny_row_limit_service._max_dataset_rows = 2

    app.dependency_overrides[get_dataset_service] = lambda: tiny_row_limit_service
    try:
        content = b"region,sales\nDelhi,1\nMumbai,2\nBangalore,3\n"  # 3 rows > limit of 2
        response = client.post("/api/v1/datasets", files={"file": ("sales.csv", content, "text/csv")}, headers=registered_user["headers"])
    finally:
        app.dependency_overrides.pop(get_dataset_service, None)

    assert response.status_code == 400


def test_upload_path_traversal_filename_is_sanitized(client: TestClient, registered_user) -> None:
    """Never trust '../../something' -- sanitize_filename (utils/file_utils.py,
    unchanged since Phase 2) strips directory components; this just
    confirms the v1 upload path still goes through it and never lets a
    traversal-shaped name reach the stored filename."""

    response = client.post(
        "/api/v1/datasets",
        files={"file": ("../../../etc/passwd.csv", b"region,sales\nDelhi,100\n", "text/csv")},
        headers=registered_user["headers"],
    )
    assert response.status_code == 201
    assert response.json()["metadata"]["filename"] == "passwd.csv"
    assert ".." not in response.json()["metadata"]["filename"]


def test_analysis_data_survives_simulated_restart(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    """Persistence after restart: dispose the engine and drop every
    process-wide cache that normally lives for the app's lifetime
    (get_settings/get_engine/get_session_factory), then reopen against
    the SAME sqlite file -- the closest a same-process test can get to
    "the application restarted" without literally spawning a new process,
    while still exercising the real disposal/reconnect path."""

    _patch_worker_graph(monkeypatch)
    created = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"}, headers=registered_user["headers"])
    analysis_id = created.json()["analysis_id"]

    from app.core.config import get_settings
    from app.db.session import get_engine, get_session_factory

    asyncio_run(get_engine().dispose())
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    get_settings.cache_clear()  # DATABASE_URL env var is unchanged -- this just forces re-reading it

    response = client.get(f"/api/v1/analysis/{analysis_id}", headers=registered_user["headers"])
    assert response.status_code == 200
    assert response.json()["answer"] == "Mumbai leads."


# ------------------------------------------------------------------ #
# ANALYSIS (job-based)
# ------------------------------------------------------------------ #


def test_create_analysis_v1_runs_to_completion_via_eager_worker(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    _patch_worker_graph(monkeypatch)

    response = client.post(
        "/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"},
        headers=registered_user["headers"],
    )
    assert response.status_code == 202
    analysis_id = response.json()["analysis_id"]
    # Celery eager mode ran the job synchronously inside .delay() above,
    # so the analysis is already in its terminal state by now.
    assert response.json()["status"] == "completed"

    detail = client.get(f"/api/v1/analysis/{analysis_id}", headers=registered_user["headers"])
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "completed"
    assert body["progress"] == 100
    assert body["answer"] == "Mumbai leads."


def test_regression_create_response_reflects_worker_write_not_stale_pre_delay_status(
    client: TestClient, registered_user, dataset, monkeypatch
) -> None:
    """REGRESSION TEST for the eager-Celery/stale-ORM bug found and fixed
    during Phase 9 development (see api/routes/analysis_jobs.py's
    `session.refresh(analysis, attribute_names=[...])` calls, and the
    Phase 9 production-readiness audit's transaction-boundary review).

    The bug: `run_analysis_job.delay(...)` in Celery's eager mode runs
    the ENTIRE job synchronously, through its own separate DB session,
    before `.delay()` even returns. The route's own in-memory `analysis`
    object (loaded before `.delay()`) is now stale -- it still says
    "queued". Setting `analysis.job.celery_task_id = ...` and committing
    WITHOUT refreshing first would silently write the stale "queued"
    values for every other column back over the worker's completed
    result, undoing it. This test fails immediately (asserting
    "queued" != "completed") if that refresh is ever removed.
    """

    _patch_worker_graph(monkeypatch)

    response = client.post(
        "/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"},
        headers=registered_user["headers"],
    )
    analysis_id = response.json()["analysis_id"]

    # The create endpoint's OWN response must already reflect the
    # worker's write (not the "queued" value that was true the instant
    # before `.delay()` was called).
    assert response.json()["status"] == "completed", (
        "regression: the route returned the stale pre-.delay() status instead of "
        "refreshing from the DB after the eager worker already completed the job"
    )

    # And a completely independent read (a fresh GET, fresh session) must
    # agree -- proving the correct value actually made it to the
    # database, not just into this one response object.
    detail = client.get(f"/api/v1/analysis/{analysis_id}", headers=registered_user["headers"]).json()
    assert detail["status"] == "completed"
    assert detail["answer"] is not None, "regression: a clobbered write would leave answer=None despite status=completed being faked"


def test_create_analysis_v1_question_too_long_rejected(client: TestClient, registered_user, dataset) -> None:
    from app.core.config import get_settings

    long_question = "x" * (get_settings().max_question_length + 1)
    response = client.post(
        "/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": long_question}, headers=registered_user["headers"]
    )
    assert response.status_code == 422


def test_create_analysis_v1_unknown_dataset_returns_404(client: TestClient, registered_user) -> None:
    response = client.post(
        "/api/v1/analysis", json={"dataset_id": str(uuid.uuid4()), "question": "anything"}, headers=registered_user["headers"]
    )
    assert response.status_code == 404


def test_idempotency_key_returns_existing_analysis(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    _patch_worker_graph(monkeypatch)
    headers = {**registered_user["headers"], "Idempotency-Key": "replay-key-1"}

    first = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"}, headers=headers)
    second = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"}, headers=headers)

    assert first.json()["analysis_id"] == second.json()["analysis_id"]


def test_idempotency_key_conflict_on_different_question(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    _patch_worker_graph(monkeypatch)
    headers = {**registered_user["headers"], "Idempotency-Key": "replay-key-2"}

    client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"}, headers=headers)
    conflict = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "A totally different question?"}, headers=headers)

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_expired_idempotency_key_is_treated_as_a_new_request(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    """Verify expired idempotency keys are handled safely: an old key
    past Settings.idempotency_key_ttl_seconds is freed for reuse (a new
    Analysis is created) rather than either replaying the stale result
    forever or raising a database error."""

    from datetime import timedelta

    from app.core.config import get_settings
    from app.db.models.analysis import Analysis
    from app.db.session import get_session_factory

    _patch_worker_graph(monkeypatch)
    headers = {**registered_user["headers"], "Idempotency-Key": "will-expire"}

    first = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"}, headers=headers)
    first_id = first.json()["analysis_id"]

    # Backdate it past the TTL directly -- faster and more precise than
    # actually waiting Settings.idempotency_key_ttl_seconds in real time.
    settings = get_settings()

    async def _backdate():
        async with get_session_factory()() as session:
            analysis = await session.get(Analysis, uuid.UUID(first_id))
            analysis.created_at = analysis.created_at - timedelta(seconds=settings.idempotency_key_ttl_seconds + 60)
            await session.commit()

    asyncio_run(_backdate())

    second = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "A different question entirely?"}, headers=headers)

    assert second.status_code == 202
    assert second.json()["analysis_id"] != first_id


def test_analysis_history_lists_and_paginates(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    _patch_worker_graph(monkeypatch)
    for _ in range(3):
        client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"}, headers=registered_user["headers"])

    response = client.get("/api/v1/analysis?page=1&page_size=2", headers=registered_user["headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["analyses"]) == 2
    assert body["page"] == 1 and body["page_size"] == 2


def test_analysis_history_invalid_page_size_rejected(client: TestClient, registered_user) -> None:
    response = client.get("/api/v1/analysis?page_size=1000", headers=registered_user["headers"])
    assert response.status_code == 422


def test_cancel_already_completed_analysis_returns_completed(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    _patch_worker_graph(monkeypatch)
    created = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"}, headers=registered_user["headers"])
    analysis_id = created.json()["analysis_id"]

    response = client.post(f"/api/v1/analysis/{analysis_id}/cancel", headers=registered_user["headers"])
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_approve_when_not_waiting_rejected(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    _patch_worker_graph(monkeypatch)
    created = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"}, headers=registered_user["headers"])
    analysis_id = created.json()["analysis_id"]

    response = client.post(f"/api/v1/analysis/{analysis_id}/approve", json={"approved": True}, headers=registered_user["headers"])
    assert response.status_code == 422


def test_human_approval_interrupt_and_resume_via_worker(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    """Low reviewer confidence pauses the job at waiting_for_approval;
    approving resumes the SAME checkpointed thread and reaches completed.

    Regression: this used to patch in a shared checkpointer, which hid
    that the real local-mode checkpointer_context() gave each job a fresh
    one -- so in the real app every approval failed with LangGraph's
    "Must write to at least one of [...]". It now uses the real one."""

    _patch_worker_graph(monkeypatch, reviewer_model=FakeStructuredModel(_review(confidence=0.5)))

    created = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"}, headers=registered_user["headers"])
    analysis_id = created.json()["analysis_id"]
    assert created.json()["status"] == "waiting_for_approval"

    detail = client.get(f"/api/v1/analysis/{analysis_id}", headers=registered_user["headers"])
    assert detail.json()["status"] == "waiting_for_approval"

    approved = client.post(f"/api/v1/analysis/{analysis_id}/approve", json={"approved": True}, headers=registered_user["headers"])
    assert approved.status_code == 200
    assert approved.json()["status"] == "completed"


def test_resume_after_lost_checkpoint_fails_with_clear_message(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    """If the paused state is gone (local mode + backend restart), approving
    must fail with an explanation, not LangGraph's cryptic error."""

    _patch_worker_graph(monkeypatch, reviewer_model=FakeStructuredModel(_review(confidence=0.5)), simulate_restart_between_jobs=True)

    created = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"}, headers=registered_user["headers"])
    analysis_id = created.json()["analysis_id"]
    assert created.json()["status"] == "waiting_for_approval"

    client.post(f"/api/v1/analysis/{analysis_id}/approve", json={"approved": True}, headers=registered_user["headers"])

    detail = client.get(f"/api/v1/analysis/{analysis_id}", headers=registered_user["headers"]).json()
    assert detail["status"] == "failed"
    assert "paused state was lost" in detail["error"]
    assert "Must write to at least one of" not in detail["error"]


def test_stream_for_already_completed_analysis_returns_terminal_event_without_redis(
    client: TestClient, registered_user, dataset, monkeypatch
) -> None:
    """Production-readiness audit fix: Redis Pub/Sub doesn't replay
    missed messages, so an analysis that's already finished (the eager
    worker means EVERY analysis in this test suite is already finished
    by the time the create call returns) must not require Redis just to
    report "it's done" -- the SSE endpoint checks the DB status first."""

    _patch_worker_graph(monkeypatch)
    created = client.post("/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"}, headers=registered_user["headers"])
    analysis_id = created.json()["analysis_id"]
    assert created.json()["status"] == "completed"

    response = client.get(f"/api/v1/analysis/{analysis_id}/stream", headers=registered_user["headers"])
    assert response.status_code == 200
    assert "event: analysis_completed" in response.text
    assert "data:" in response.text


def test_stream_without_redis_for_a_still_running_analysis_returns_422(client: TestClient, registered_user, dataset, monkeypatch) -> None:
    """Without Redis, an analysis genuinely still in flight (queued/running)
    has no live event transport available -- this is the one case that
    legitimately needs Redis (see core/startup_checks.py's "REDIS_URL is
    required ... SSE event transport" note)."""

    import uuid as _uuid

    from app.db.models.analysis import Analysis, AnalysisStatus
    from app.db.session import get_session_factory

    _patch_worker_graph(monkeypatch)
    created = client.post(
        "/api/v1/analysis", json={"dataset_id": dataset["dataset_id"], "question": "Which region has the highest sales?"},
        headers=registered_user["headers"],
    )
    analysis_id = created.json()["analysis_id"]

    # Force it back to "running" directly in the DB -- simulating a
    # status check that arrives while a REAL (non-eager) worker is still
    # mid-flight, which this synchronous eager-mode test suite can't
    # otherwise produce.
    async def _force_running():
        async with get_session_factory()() as session:
            analysis = await session.get(Analysis, _uuid.UUID(analysis_id))
            analysis.status = AnalysisStatus.RUNNING
            await session.commit()

    asyncio_run(_force_running())

    response = client.get(f"/api/v1/analysis/{analysis_id}/stream", headers=registered_user["headers"])
    assert response.status_code == 422


# ------------------------------------------------------------------ #
# REDIS: rate limiting, caching
# ------------------------------------------------------------------ #


def test_rate_limit_blocks_after_configured_count(client: TestClient, registered_user, monkeypatch, fake_redis) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "upload_rate_limit", "2/minute")

    responses = [
        client.post("/api/v1/datasets", files={"file": ("notes.txt", b"hello", "text/plain")}, headers=registered_user["headers"])
        for _ in range(3)
    ]

    assert [r.status_code for r in responses[:2]] == [400, 400]
    assert responses[2].status_code == 429
    assert responses[2].json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


def test_dataset_schema_is_cached_and_invalidated_on_delete(client: TestClient, registered_user, dataset, fake_redis) -> None:
    dataset_id = dataset["dataset_id"]

    first = client.get(f"/api/v1/datasets/{dataset_id}", headers=registered_user["headers"])
    assert first.status_code == 200
    cache_key = f"dataset_schema:{dataset_id}:{dataset['version']}"
    assert asyncio_run(fake_redis.get(cache_key)) is not None

    client.delete(f"/api/v1/datasets/{dataset_id}", headers=registered_user["headers"])
    assert asyncio_run(fake_redis.get(cache_key)) is None


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
