"""Entrypoint for a Hugging Face Space using the (free) Gradio SDK.

The Gradio SDK just runs `python app.py` and routes public traffic to port
7860 -- this file doesn't use Gradio at all. It starts everything the
backend needs inside the one free container:

  redis-server (apt package from packages.txt; memory only)
  -> alembic upgrade head
  -> restore datasets from Postgres (the Space's disk is wiped on restart)
  -> Celery worker + FastAPI API on port 7860

If the worker or API exits, this script exits too so the Space restarts
(a running API with a dead worker would accept jobs that never finish).
"""

import os
import re
import subprocess
import sys
import time

# Production settings that are fixed for this deployment. Secrets
# (DATABASE_URL, OPENAI_API_KEY, CORS_ALLOWED_ORIGINS) come from the
# Space's "Variables and secrets" settings.
os.environ.setdefault("ENVIRONMENT", "production")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("LLM_PROVIDER", "openai")
os.environ.setdefault("MAX_FILE_SIZE_MB", "10")  # keeps stored files within Neon's free 0.5GB
os.environ.setdefault("PYTHONUNBUFFERED", "1")

database_url = os.environ.get("DATABASE_URL", "")
if not database_url:
    sys.exit("DATABASE_URL is not set -- add it as a secret in the Space settings.")
# Neon hands out postgres:// or postgresql:// URLs; the app needs SQLAlchemy's driver prefix.
os.environ["DATABASE_URL"] = re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", database_url)


def run(*cmd: str) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


run("redis-server", "--daemonize", "yes", "--bind", "127.0.0.1", "--save", "", "--appendonly", "no")
run(sys.executable, "-m", "alembic", "upgrade", "head")
run(sys.executable, "-m", "app.scripts.restore_datasets")

processes = [
    subprocess.Popen([sys.executable, "-m", "celery", "-A", "app.workers.celery_app", "worker",
                      "--loglevel=info", "--concurrency=2"]),
    subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860",
                      "--proxy-headers", "--forwarded-allow-ips=*"]),
]

while all(p.poll() is None for p in processes):
    time.sleep(2)

for p in processes:
    if p.poll() is None:
        p.terminate()
sys.exit(1)
