"""get_redis_service() hands out one client per event loop (the worker runs
each task on a fresh loop; a redis.asyncio connection is bound to the loop
that opened it), and one shared client when called with no loop running
(FastAPI resolves sync dependencies on a threadpool thread)."""

import asyncio

import pytest

from app.core.config import get_settings
from app.services.redis_service import get_redis_service


@pytest.fixture
def redis_configured(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")  # never connected to in these tests
    get_settings.cache_clear()
    get_redis_service.cache_clear()
    yield
    get_redis_service.cache_clear()
    get_settings.cache_clear()


def test_same_loop_reuses_client_and_new_loop_gets_new_one(redis_configured) -> None:
    async def twice():
        return get_redis_service(), get_redis_service()

    first_a, first_b = asyncio.run(twice())
    second_a, _ = asyncio.run(twice())
    assert first_a is first_b
    assert first_a is not second_a


def test_no_running_loop_returns_one_shared_client(redis_configured) -> None:
    assert get_redis_service() is get_redis_service()


def test_returns_none_without_redis_url(monkeypatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    get_settings.cache_clear()
    try:
        assert get_redis_service() is None
    finally:
        get_settings.cache_clear()
