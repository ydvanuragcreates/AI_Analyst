"""The in-process Celery worker (workers/embedded.py) used by the
single-container deployment (scripts/serve.py) actually consumes queued
tasks -- tested against kombu's in-memory broker, so no Redis is needed."""

import asyncio
import threading

from celery import Celery

from app.workers.embedded import start_embedded_worker


def test_embedded_worker_runs_queued_tasks_on_its_own_threads() -> None:
    app = Celery("embedded_test", broker="memory://", backend="cache+memory://")
    app.conf.task_always_eager = False

    results: list[tuple[int, str]] = []
    done = threading.Event()

    @app.task(name="embedded_test.record")
    def record(value: int) -> None:
        # Same shape as analysis_worker._run_async: each task runs its own event loop.
        async def work() -> int:
            await asyncio.sleep(0)
            return value * 2

        results.append((asyncio.run(work()), threading.current_thread().name))
        if len(results) == 3:
            done.set()

    thread = start_embedded_worker(app, concurrency=2, loglevel="WARNING")
    assert thread.is_alive()

    for value in (1, 2, 3):
        record.delay(value)

    assert done.wait(timeout=20), f"only {len(results)} of 3 tasks ran"
    assert sorted(r for r, _ in results) == [2, 4, 6]
    assert all(name != threading.main_thread().name for _, name in results)
