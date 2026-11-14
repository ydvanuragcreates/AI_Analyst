# Render (free web service) image: API + embedded Celery worker + local Redis
# in ONE process/container -- see backend/app/scripts/serve.py for why.
# Build context is backend/ (set in render.yaml).

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends redis-server \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 appuser
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN mkdir -p storage/datasets storage/duckdb && chown -R appuser:appuser /app

USER appuser
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MALLOC_ARENA_MAX=2

CMD ["python", "-m", "app.scripts.serve"]
