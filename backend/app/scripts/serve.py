"""Single-container, single-process production entrypoint.

    python -m app.scripts.serve

For free hosts that give one small container with a disk wiped on restart
(e.g. Render's free web service). In order:

  1. local redis-server (memory only: broker / cache / rate limits / SSE)
  2. alembic upgrade head            (own process; its memory is freed after)
  3. restore datasets from Postgres  (own process; see restore_datasets.py)
  4. Celery worker on a thread of THIS process (workers/embedded.py)
  5. the API (uvicorn) on $PORT

Postgres (e.g. Neon) is external and holds everything that must survive a
restart. Secrets come from the host's environment settings:
DATABASE_URL, OPENAI_API_KEY, CORS_ALLOWED_ORIGINS.
"""

import os
import re
import subprocess
import sys
import threading
import time

# Fixed settings for this deployment shape -- set before any app import,
# since Settings is read once and cached.
os.environ.setdefault("ENVIRONMENT", "production")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("LLM_PROVIDER", "openai")
os.environ.setdefault("MAX_FILE_SIZE_MB", "10")  # stored files must fit Neon's free 0.5GB
os.environ.setdefault("DB_DISABLE_POOLING", "true")  # worker tasks run on their own event loops
os.environ.setdefault("PYTHONUNBUFFERED", "1")
# Public-demo guards (override in the host's environment settings if needed).
os.environ.setdefault("DAILY_ANALYSIS_LIMIT", "50")      # protects the OpenAI budget
os.environ.setdefault("DATASET_RETENTION_DAYS", "7")     # keeps Neon under its free 0.5GB
os.environ.setdefault("ENABLE_LEGACY_API", "false")      # uncapped sync LLM routes; web app uses /api/v1
# Absolute paths: relative ones resolve against the project root (backend/'s
# parent), which inside the image is "/" -- not writable.
os.environ.setdefault("DATASET_STORAGE_PATH", "/app/storage/datasets")
os.environ.setdefault("DUCKDB_PATH", "/app/storage/duckdb/analytics.duckdb")


def _clean_database_url(raw: str) -> str:
    """Accept what people actually paste: Neon's `psql '...'` snippet, stray
    quotes, the pooled endpoint, or a plain postgres:// URL."""

    url = re.sub(r"^psql\s+", "", raw.strip()).strip().strip("'\"").strip()
    if "-pooler." in url:
        # LangGraph's Postgres checkpointer needs session-level features
        # that a transaction pooler (PgBouncer) doesn't keep.
        url = url.replace("-pooler.", ".", 1)
        print("DATABASE_URL: using Neon's direct (non-pooled) endpoint", flush=True)
    return re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", url)


def _run(*cmd: str) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main() -> None:
    database_url = _clean_database_url(os.environ.get("DATABASE_URL", ""))
    if not database_url:
        sys.exit("DATABASE_URL is not set -- add it in the host's environment settings.")
    os.environ["DATABASE_URL"] = database_url

    _run("redis-server", "--daemonize", "yes", "--bind", "127.0.0.1", "--save", "", "--appendonly", "no",
         "--maxmemory", "48mb", "--maxmemory-policy", "noeviction")
    for _ in range(50):
        if subprocess.run(["redis-cli", "ping"], capture_output=True, text=True).stdout.strip() == "PONG":
            break
        time.sleep(0.1)

    _run(sys.executable, "-m", "alembic", "upgrade", "head")
    _run(sys.executable, "-m", "app.scripts.restore_datasets")

    from app.workers.celery_app import celery_app
    from app.workers.embedded import start_embedded_worker

    worker_thread = start_embedded_worker(celery_app, concurrency=int(os.environ.get("WORKER_CONCURRENCY", "2")))

    def _exit_if_worker_dies() -> None:
        # A running API with a dead worker would accept jobs that never finish;
        # exit so the platform restarts the container instead.
        worker_thread.join()
        print("Embedded worker stopped; exiting so the platform restarts the service", flush=True)
        os._exit(1)

    threading.Thread(target=_exit_if_worker_dies, daemon=True).start()

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
