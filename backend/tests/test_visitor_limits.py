"""Per-visitor limits and spoof-resistant caller identification
(core/client_ip.py): per-visitor daily analysis cap, global daily token
budget, per-visitor daily upload cap."""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from app.core.client_ip import client_ip, client_key
from app.core.config import get_settings
from app.db.models.analysis import Analysis
from app.db.models.dataset import Dataset
from app.db.repositories.analysis_repository import AnalysisRepository
from app.db.session import get_session_factory
from app.main import app


def _request(headers: dict[str, str], peer: str = "10.0.0.1") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 1234)})


def _settings(**overrides):
    return get_settings().model_copy(update=overrides)


# ------------------------------------------------------------------ #
# Caller identification
# ------------------------------------------------------------------ #

def test_platform_header_wins_over_spoofable_forwarded_for() -> None:
    # Render/Cloudflare: True-Client-IP is overwritten by the edge; XFF's
    # leftmost entry is whatever the caller sent.
    request = _request({"x-forwarded-for": "203.0.113.77, 183.83.143.187, 162.159.114.88",
                        "true-client-ip": "183.83.143.187"})
    assert client_ip(request, _settings(client_ip_header="true-client-ip")) == "183.83.143.187"


def test_trusted_proxy_hops_reads_from_the_right() -> None:
    request = _request({"x-forwarded-for": "203.0.113.77, 183.83.143.187, 162.159.114.88, 10.28.171.85"})
    assert client_ip(request, _settings(trusted_proxy_hops=3)) == "183.83.143.187"


def test_defaults_to_direct_peer_and_ignores_forwarded_headers() -> None:
    request = _request({"x-forwarded-for": "203.0.113.77", "true-client-ip": "203.0.113.88"}, peer="192.0.2.5")
    assert client_ip(request, _settings()) == "192.0.2.5"


def test_missing_platform_header_falls_back_to_peer() -> None:
    assert client_ip(_request({}, peer="192.0.2.5"), _settings(client_ip_header="true-client-ip")) == "192.0.2.5"


def test_client_key_is_a_hash_not_the_ip() -> None:
    key = client_key(_request({}, peer="192.0.2.5"), _settings())
    assert "192.0.2.5" not in key and len(key) == 32


# ------------------------------------------------------------------ #
# Limits (API level)
# ------------------------------------------------------------------ #

_uploaded: list[str] = []


@pytest.fixture
def client(sqlite_db) -> TestClient:
    yield TestClient(app)
    from app.services.dataset_service import get_dataset_service
    from app.services.exceptions import DatasetNotFoundError

    while _uploaded:
        try:
            get_dataset_service().delete_dataset(_uploaded.pop())
        except DatasetNotFoundError:
            pass


@pytest.fixture
def limits():
    def apply(**overrides) -> None:
        settings = _settings(client_ip_header="true-client-ip", **overrides)
        app.dependency_overrides[get_settings] = lambda: settings
        # The upload route reads get_settings() directly, not via Depends.
        import app.api.routes.datasets_v1 as datasets_v1

        datasets_v1.get_settings = lambda: settings

    yield apply
    app.dependency_overrides.pop(get_settings, None)
    import app.api.routes.datasets_v1 as datasets_v1

    datasets_v1.get_settings = get_settings


@pytest.fixture
def no_worker(monkeypatch):
    import app.workers.analysis_worker as worker

    monkeypatch.setattr(worker.run_analysis_job, "delay", lambda *args, **kwargs: None)


def _upload(client: TestClient, ip: str = "198.51.100.1"):
    with open("tests/fixtures/sales.csv", "rb") as fh:
        response = client.post("/api/v1/datasets", files={"file": ("sales.csv", fh, "text/csv")},
                               headers={"True-Client-IP": ip})
    if response.status_code == 201:
        _uploaded.append(response.json()["dataset_id"])
    return response


def _analyze(client: TestClient, dataset_id: str, ip: str):
    return client.post("/api/v1/analysis", json={"dataset_id": dataset_id, "question": "Top region?"},
                       headers={"True-Client-IP": ip})


def test_per_visitor_analysis_cap_is_per_ip(client, limits, no_worker) -> None:
    limits(daily_analysis_limit_per_client=2)
    dataset_id = _upload(client).json()["dataset_id"]

    assert _analyze(client, dataset_id, "198.51.100.1").status_code == 202
    assert _analyze(client, dataset_id, "198.51.100.1").status_code == 202
    blocked = _analyze(client, dataset_id, "198.51.100.1")
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "DAILY_LIMIT_REACHED"
    # Another visitor is unaffected.
    assert _analyze(client, dataset_id, "198.51.100.2").status_code == 202


def test_per_visitor_cap_cannot_be_dodged_with_forwarded_for(client, limits, no_worker) -> None:
    limits(daily_analysis_limit_per_client=1)
    dataset_id = _upload(client).json()["dataset_id"]

    def spoofed(fake: str):
        return client.post("/api/v1/analysis", json={"dataset_id": dataset_id, "question": "q"},
                           headers={"True-Client-IP": "198.51.100.9", "X-Forwarded-For": fake})

    assert spoofed("203.0.113.1").status_code == 202
    assert spoofed("203.0.113.2").status_code == 429


def test_daily_token_budget_blocks_new_analyses(client, limits, no_worker) -> None:
    dataset_id = _upload(client).json()["dataset_id"]

    async def record_usage(tokens: int) -> None:
        async with get_session_factory()() as session:
            dataset = (await session.execute(select(Dataset).where(Dataset.id == uuid.UUID(dataset_id)))).scalar_one()
            analysis = await AnalysisRepository(session).create(
                user_id=dataset.user_id, dataset_id=dataset.id, dataset_version=1,
                thread_id=str(uuid.uuid4()), question="q", idempotency_key=None,
            )
            analysis.token_usage = {"input_tokens": tokens - 10, "output_tokens": 10, "total_tokens": tokens}
            await session.commit()

    asyncio.run(record_usage(600))
    limits(daily_token_budget=1000)
    assert _analyze(client, dataset_id, "198.51.100.3").status_code == 202  # 600 < 1000

    asyncio.run(record_usage(500))  # 1100 >= 1000
    blocked = _analyze(client, dataset_id, "198.51.100.3")
    assert blocked.status_code == 429 and "budget" in blocked.json()["error"]["message"]


def test_per_visitor_upload_cap(client, limits) -> None:
    limits(daily_upload_limit_per_client=2)

    assert _upload(client, "198.51.100.4").status_code == 201
    assert _upload(client, "198.51.100.4").status_code == 201
    blocked = _upload(client, "198.51.100.4")
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "DAILY_LIMIT_REACHED"
    assert _upload(client, "198.51.100.5").status_code == 201


def test_client_key_is_stored_on_rows(client, limits, no_worker) -> None:
    limits()
    dataset_id = _upload(client, "198.51.100.6").json()["dataset_id"]
    analysis_id = _analyze(client, dataset_id, "198.51.100.6").json()["analysis_id"]

    async def keys():
        async with get_session_factory()() as session:
            d = (await session.execute(select(Dataset.client_key).where(Dataset.id == uuid.UUID(dataset_id)))).scalar_one()
            a = (await session.execute(select(Analysis.client_key).where(Analysis.id == uuid.UUID(analysis_id)))).scalar_one()
        return d, a

    dataset_key, analysis_key = asyncio.run(keys())
    assert dataset_key == analysis_key and dataset_key and "198.51.100.6" not in dataset_key
