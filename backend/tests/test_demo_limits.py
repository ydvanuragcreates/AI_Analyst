"""Public-demo guards: the global daily analysis cap (Settings.daily_analysis_limit)
and dataset retention (services/retention.py)."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.config import get_settings
from app.db.models.analysis import Analysis
from app.db.models.dataset import Dataset
from app.db.models.dataset_file import DatasetFile
from app.db.repositories.analysis_repository import AnalysisRepository
from app.db.session import get_session_factory
from app.main import app
from app.services.retention import purge_expired_datasets

pytestmark = pytest.mark.usefixtures("sqlite_db")


_uploaded: list[str] = []


@pytest.fixture
def client() -> TestClient:
    yield TestClient(app)
    # Remove local copies directly: the API refuses to delete datasets that
    # have analyses, and dataset storage is shared across the test session.
    from app.services.dataset_service import get_dataset_service
    from app.services.exceptions import DatasetNotFoundError

    while _uploaded:
        try:
            get_dataset_service().delete_dataset(_uploaded.pop())
        except DatasetNotFoundError:
            pass


def _upload(client: TestClient) -> dict:
    with open("tests/fixtures/sales.csv", "rb") as fh:
        response = client.post("/api/v1/datasets", files={"file": ("sales.csv", fh, "text/csv")})
    assert response.status_code == 201, response.text
    _uploaded.append(response.json()["dataset_id"])
    return response.json()


async def _add_analysis(dataset_id: str, *, created_at: datetime | None = None) -> uuid.UUID:
    async with get_session_factory()() as session:
        dataset = (await session.execute(select(Dataset).where(Dataset.id == uuid.UUID(dataset_id)))).scalar_one()
        analysis = await AnalysisRepository(session).create(
            user_id=dataset.user_id, dataset_id=dataset.id, dataset_version=dataset.version,
            thread_id=str(uuid.uuid4()), question="q", idempotency_key=None, mode="qa",
        )
        if created_at is not None:
            analysis.created_at = created_at
        await session.commit()
        return analysis.id


async def _age_dataset(dataset_id: str, days: int) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            update(Dataset).where(Dataset.id == uuid.UUID(dataset_id))
            .values(created_at=datetime.now(timezone.utc) - timedelta(days=days))
        )
        await session.commit()


@pytest.fixture
def daily_limit():
    """Override the route's Settings dependency with a given daily cap."""

    def set_limit(limit: int) -> None:
        settings = get_settings().model_copy(update={"daily_analysis_limit": limit})
        app.dependency_overrides[get_settings] = lambda: settings

    yield set_limit
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def no_worker(monkeypatch):
    """Don't run the (LLM-backed) job; only the create route is under test."""
    import app.workers.analysis_worker as worker

    monkeypatch.setattr(worker.run_analysis_job, "delay", lambda *args, **kwargs: None)


def test_daily_cap_rejects_once_todays_limit_is_reached(client, daily_limit, no_worker) -> None:
    dataset_id = _upload(client)["dataset_id"]
    asyncio.run(_add_analysis(dataset_id))
    daily_limit(1)

    response = client.post("/api/v1/analysis", json={"dataset_id": dataset_id, "question": "Top region?"})

    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "DAILY_LIMIT_REACHED"
    assert "per day" in error["message"]


def test_daily_cap_ignores_previous_days(client, daily_limit, no_worker) -> None:
    dataset_id = _upload(client)["dataset_id"]
    asyncio.run(_add_analysis(dataset_id, created_at=datetime.now(timezone.utc) - timedelta(days=1, hours=1)))
    daily_limit(1)

    response = client.post("/api/v1/analysis", json={"dataset_id": dataset_id, "question": "Top region?"})

    assert response.status_code == 202, response.text


def test_zero_means_unlimited(client, daily_limit, no_worker) -> None:
    dataset_id = _upload(client)["dataset_id"]
    for _ in range(3):
        asyncio.run(_add_analysis(dataset_id))
    daily_limit(0)

    assert client.post("/api/v1/analysis", json={"dataset_id": dataset_id, "question": "q"}).status_code == 202


def test_retention_deletes_old_datasets_with_their_analyses_and_keeps_recent(client) -> None:
    old_id = _upload(client)["dataset_id"]
    recent_id = _upload(client)["dataset_id"]
    old_analysis = asyncio.run(_add_analysis(old_id))
    asyncio.run(_age_dataset(old_id, days=10))

    async def purge_and_inspect():
        async with get_session_factory()() as session:
            deleted = await purge_expired_datasets(session, retention_days=7)
        async with get_session_factory()() as session:
            datasets = set((await session.execute(select(Dataset.id))).scalars())
            files = set((await session.execute(select(DatasetFile.dataset_id))).scalars())
            analyses = set((await session.execute(select(Analysis.id))).scalars())
        return deleted, datasets, files, analyses

    deleted, datasets, files, analyses = asyncio.run(purge_and_inspect())

    assert deleted == [uuid.UUID(old_id)]
    assert uuid.UUID(old_id) not in datasets and uuid.UUID(recent_id) in datasets
    assert uuid.UUID(old_id) not in files and uuid.UUID(recent_id) in files
    assert old_analysis not in analyses


def test_retention_disabled_by_default(client) -> None:
    dataset_id = _upload(client)["dataset_id"]
    asyncio.run(_age_dataset(dataset_id, days=365))

    async def purge():
        async with get_session_factory()() as session:
            return await purge_expired_datasets(session, retention_days=get_settings().dataset_retention_days)

    assert asyncio.run(purge()) == []
