"""
Shared pytest fixtures.

Sets a dummy OPENAI_API_KEY before app.main is imported anywhere (config.py
requires it), so the suite runs out of the box without manual env setup. A
real key, if already exported, is left untouched.
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import fakeredis
import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FakeStructuredModel:
    """Test double for model.with_structured_output(SomeSchema).

    Real with_structured_output() isn't implemented on LangChain's fake
    chat models (verified directly: it raises NotImplementedError, same
    as bind_tools() does), so graph/API tests that need a stand-in for
    a *structured-output* stage use this instead -- it satisfies the same
    `.invoke(messages) -> <schema instance>` interface every structured
    node calls (build_final_analysis_node, and Phase 6's
    build_analyze_question_node/build_create_sql_plan_node/
    build_generate_sql_node/build_validate_result_node/
    build_workflow_final_response_node), without going through any real
    structured-output machinery. The dedicated, real with_structured_output()
    tests live in test_structured_output.py and need an actual API key.

    Pass a single `response` to always return the same value (most
    tests), or `responses` (a list) to return a different value on each
    successive call -- needed for retry-scenario tests, e.g. a
    generate_sql fake that returns bad SQL on attempt 1 and corrected SQL
    on attempt 2. Once the list is exhausted, the last value repeats.
    """

    def __init__(self, response: Any = None, responses: list[Any] | None = None) -> None:
        if responses is not None:
            self._responses = list(responses)
        elif response is not None:
            self._responses = [response]
        else:
            raise ValueError("FakeStructuredModel requires either response= or responses=")
        self._call_count = 0

    def invoke(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        index = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return self._responses[index]

_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".json": "application/json",
    ".txt": "text/plain",
}


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def upload_fixture(client: TestClient):
    """Upload a file from tests/fixtures/ and auto-delete it afterwards.

    Returns a callable: upload_fixture("sales.csv") -> httpx.Response.
    Any dataset successfully created during the test is deleted in
    teardown, so tests don't need to manage cleanup themselves.
    """

    created_ids: list[str] = []

    def _upload(filename: str):
        file_path = FIXTURES_DIR / filename
        content_type = _CONTENT_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
        with file_path.open("rb") as fh:
            response = client.post(
                "/api/datasets/upload",
                files={"file": (filename, fh, content_type)},
            )
        if response.status_code == 201:
            created_ids.append(response.json()["dataset_id"])
        return response

    yield _upload

    for dataset_id in created_ids:
        client.delete(f"/api/datasets/{dataset_id}")


@pytest.fixture
def sqlite_db(tmp_path):
    """Point Settings.database_url / db.session's cached engine at a real
    (SQLite, temp file) database for the duration of one test, with every
    Phase 9 table created -- there is no Postgres available in this
    environment (no Docker), so this is what "tested" means for the DB
    layer (see db/base.py's module docstring on the GUID type this
    depends on for cross-dialect portability).

    Explicitly clears the `@lru_cache`s on get_settings/get_engine/
    get_session_factory before AND after -- those are process-wide
    singletons everywhere else in this codebase (deliberately, e.g.
    get_duckdb_service/get_chat_model), so a test that wants a different
    DATABASE_URL than "unconfigured" (every other test's default) has to
    reset them explicitly, both to pick up the new value and to not leak
    it into whichever test runs next.
    """

    from app.core.config import get_settings
    from app.db.models import Base
    from app.db.session import get_engine, get_session_factory

    db_path = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    async def _create_all():
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())

    yield get_session_factory()

    asyncio.run(get_engine().dispose())
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()


@pytest.fixture
def fake_redis(monkeypatch):
    """A working (in-memory, fakeredis-backed) RedisService for one test,
    exercising the real RedisService/rate-limit/caching/event code paths
    against a real redis-py-compatible client -- there is no Redis server
    available in this environment either.

    Two different patch mechanisms, because get_redis_service() is used
    two different ways in this codebase:
    - As a FastAPI dependency (`Depends(get_redis_service)`, used by
      every v1 route and by middleware/rate_limit.py's nested
      dependency) -- overridden via `app.dependency_overrides`, the
      same mechanism every other test in this suite already uses for
      swapping a dependency (e.g. get_multi_agent_graph).
    - As a plain function call in worker code that runs OUTSIDE any
      FastAPI request (workers/analysis_worker.py, invoked here via
      Celery's eager mode within the same test process) -- `monkeypatch`
      is what's needed there, since dependency_overrides only affects
      FastAPI's own DI resolution.
    """

    import app.workers.analysis_worker as analysis_worker_module
    from app.services.redis_service import RedisService, get_redis_service

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    service = RedisService(client)

    app.dependency_overrides[get_redis_service] = lambda: service
    monkeypatch.setattr(analysis_worker_module, "get_redis_service", lambda: service)

    yield service

    app.dependency_overrides.pop(get_redis_service, None)
    asyncio.run(client.aclose())
