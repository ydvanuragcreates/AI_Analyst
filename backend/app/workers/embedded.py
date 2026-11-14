"""Run a Celery worker on a background thread of the current process.

For hosts that give one small container (e.g. Render's free tier, 512MB):
every Python process here costs ~200MB just to import the stack, so a
separate `celery worker` process next to the API doesn't fit. Instead the
API process consumes the same Redis queue itself -- jobs are still queued,
retried and reported exactly as with a separate worker.

Uses Celery's own WorkController (the component `celery worker` wraps)
with the "threads" pool; the same approach as Celery's
`celery.contrib.testing.worker`. WorkController installs no signal
handlers, so it's safe off the main thread.

Caveat: thread pools can't enforce `task_soft_time_limit` (Celery logs a
warning); the analysis graphs' own step/time budgets still apply.
"""

import logging
import threading

from celery import Celery
from celery.utils.nodenames import anon_nodename

logger = logging.getLogger(__name__)


def start_embedded_worker(app: Celery, *, concurrency: int = 2, loglevel: str = "INFO") -> threading.Thread:
    """Start a worker thread for `app`; returns once it is consuming tasks."""

    app.finalize()
    app.set_current()
    app.set_default()
    app.loader.import_default_modules()  # registers tasks listed in conf.imports

    ready = threading.Event()

    class _WorkController(app.WorkController):  # type: ignore[name-defined, misc]
        def on_consumer_ready(self, consumer):
            ready.set()

    worker = _WorkController(
        app=app,
        hostname=anon_nodename(),
        pool="threads",
        concurrency=concurrency,
        loglevel=loglevel,
        without_heartbeat=True,
        without_mingle=True,
        without_gossip=True,
    )

    thread = threading.Thread(target=worker.start, name="celery-embedded-worker", daemon=True)
    thread.start()
    if not ready.wait(timeout=60):
        raise RuntimeError("Embedded Celery worker did not become ready within 60s")
    logger.info("Embedded Celery worker ready (threads pool, concurrency=%d)", concurrency)
    return thread
