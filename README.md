# AI Data Analyst Agent

Upload a spreadsheet. Ask a question in plain English. Get a real answer —
backed by an actual query against your actual data, not a guess.

## What this actually does

Say you have a spreadsheet of sales data and want to know *"which region
made the most money last quarter?"* Normally you'd either need to know how
to write that query yourself, or paste the whole spreadsheet into a chatbot
and hope it doesn't make the numbers up.

This project does neither. You upload a file (CSV, Excel, or JSON), ask
your question in plain English, and behind the scenes an AI agent:

1. Looks at your data's **structure** (column names, types) — never the raw
   contents.
2. Writes a real **SQL query** to answer your question.
3. Checks that query is **safe** before running it (read-only — there's no
   way for it to change or delete anything).
4. Runs it against your actual data and gets a **real result**.
5. Double-checks its own answer against that real result before showing it
   to you — if the answer doesn't hold up, it tries again.

You get back a plain-English answer, the real numbers behind it, a chart if
one makes sense, and the exact SQL it ran — so anyone can check it wasn't
made up.

## Why it's more than "a chatbot for your spreadsheet"

This project exists mainly as an engineering portfolio piece, so it leans
into the parts that are easy to get wrong:

- **Never sends your raw rows to the AI** — only structure and row counts,
  so it can't leak data it doesn't need to see.
- **Every SQL query is validated as read-only** before it's ever executed.
- **The AI reviews its own answer** against the real query results before
  it reaches you, and retries if it doesn't check out.
- **Harder questions get split across specialized AI agents** — a planner,
  a SQL writer, a data analyst, a reviewer — instead of one model trying to
  do everything at once.
- **A "just go figure something out" mode**: point it at a dataset with no
  specific question and it profiles the data, forms its own hypotheses,
  tests them, and comes back with a reviewed, evidence-backed report.
- **Low-confidence or expensive answers pause for a human** to approve
  before they're treated as final.
- Built with the pieces a real production system would need: user
  accounts, background job processing, real-time progress updates, rate
  limiting, and a Docker setup to run it all together.

## Try it yourself

Two things need to run at once: the **backend** (the AI/data engine) and
the **frontend** (the web page you actually click around in).

**You'll need:** Python 3.11+, Node.js 18+, and an OpenAI API key.

### 1. Start the backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows -- use `source .venv/bin/activate` on Mac/Linux
pip install -r requirements-dev.txt

cp ../.env.example ../.env    # then open .env and paste in your OPENAI_API_KEY
uvicorn app.main:app --reload
```

Leave this running. To check it worked, open
[http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health) in a
browser — you should see `{"status":"ok"}`. Interactive API docs live at
[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

### 2. Start the frontend

Open a **second** terminal (leave the backend running in the first one):

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) and sign up for an
account.

> **If sign-up fails:** the web app needs a small database to store
> accounts, which isn't set up by the steps above. See
> [Running the full stack locally, no Docker required](#running-the-full-stack-locally-no-docker-required)
> below — it takes one command and doesn't need Postgres, Redis, or Docker
> installed.

Once you're in: upload a dataset, ask it a question, and watch it work.

## Running the full stack locally, no Docker required

The web app's login/accounts feature is normally backed by Postgres and
Redis (see [Under the hood](#under-the-hood-for-engineers) for why). For
local use, both have a genuine, tested fallback that needs nothing extra
installed:

```bash
cd backend
# in your .env file, set:
#   DATABASE_URL=sqlite+aiosqlite:///./storage/app.db
#   REDIS_URL=
alembic upgrade head
uvicorn app.main:app --reload
```

That's it — restart the backend and sign-up/login will work. One caveat:
live progress updates while an analysis runs need real Redis, so without
it the app checks progress by refreshing instead of streaming it live.
Everything else behaves the same.

If you want the production-shaped version instead (real Postgres, real
Redis, live streaming, a separate background worker), see
[Docker](#docker-production-shaped-api--worker--postgres--redis) below.

## Project layout

- **`backend/`** — FastAPI + LangGraph + OpenAI. All data processing and
  AI-agent logic lives here.
- **`frontend/`** — the Next.js web app.
- **`docker-compose.yml`** — runs the whole thing (API, background worker,
  database, cache) together in containers.

---

## Under the hood (for engineers)

<details>
<summary><strong>Full phase-by-phase build log — click to expand</strong></summary>

**Phase 1 through Phase 10 complete.**

- Phase 1: FastAPI + config + logging + a minimal LangGraph graph
  (`START -> process_question -> END`) wired to OpenAI via a centralized
  model factory.
- Phase 2: local dataset ingestion — upload CSV/XLSX/JSON, validate and
  store the file, inspect it with pandas (row/column counts, inferred
  types, missing values, preview), and serve it back through a REST API.
- Phase 3: DuckDB analytical query engine — every uploaded dataset is
  registered as a DuckDB table, queryable via a safety-validated,
  read-only SQL API (schema/sample/query endpoints). No LLM involvement.
- Phase 4: LangChain tool calling — the OpenAI model can inspect a
  dataset's schema, sample its rows, and execute validated SQL against it
  itself, via three tools (`get_dataset_schema`, `get_sample_rows`,
  `execute_sql`) and a LangGraph agent loop.
- Phase 5: structured output — `with_structured_output` (not `bind_tools`)
  turns gathered evidence into a validated `AnalysisResponse` (answer,
  key findings, metrics, the actual SQL executed, confidence bounded 0-1
  by Pydantic).
- Phase 6: `POST /api/analysis` is now backed by a deterministic,
  non-agentic LangGraph workflow instead of the autonomous tool-calling
  agent — single-purpose nodes (analyze question → inspect schema → plan
  → generate SQL → validate → execute → validate result → respond),
  separately-configured structured-output calls, and a bounded retry
  loop (`MAX_SQL_RETRIES`, default 3) driven by real LangGraph
  conditional edges. The Phase 4/5 tool-calling agent remains in the
  codebase, fully tested, as a deliberate side-by-side comparison.
- Phase 7: multi-step analysis, conversation memory, and
  visualization-ready output. The plan is now 1-5 `AnalysisStep`s
  (`MAX_ANALYSIS_STEPS`, default 5) instead of a single query, each
  executed/validated/retried independently; a deterministic (non-LLM)
  synthesis stage builds a controlled summary across every step's real
  result; chart data (`bar`/`line`/`pie`/`table`/`scatter`) is built
  deterministically from actual query rows, never invented; a LangGraph
  checkpointer (`InMemorySaver`) plus a `thread_id` enables genuine
  follow-up questions ("what about the second highest?") grounded in a
  curated summary of the previous turn, not the full raw state.
- Phase 8: `POST /api/analysis` is now backed by a multi-agent LangGraph
  graph — Supervisor (routes only) → Planner (or a deterministic
  trivial-plan shortcut) → SQL Analyst (a LangGraph *subgraph* reusing
  Phase 6/7's SQL generate/validate/execute/retry nodes unchanged) → Data
  Analyst (interprets real query results into grounded findings) →
  Reviewer (checks findings against the actual results, up to
  `MAX_REVIEW_RETRIES` correction cycles, default 2) → an optional
  human-in-the-loop approval gate. Low confidence
  (`HUMAN_APPROVAL_CONFIDENCE_THRESHOLD`, default 0.70), an expensive
  analysis (`HUMAN_APPROVAL_COST_THRESHOLD_MS`, default 2000ms), or
  exhausted correction retries pause the graph via LangGraph's
  `interrupt()`; `GET /api/analysis/{thread_id}/status` and
  `POST /api/analysis/{thread_id}/approve` (`{"approved": true}` or
  `{"approved": false, "feedback": "..."}`) check on and resume that same
  checkpointed thread — a rejection flows back through the Planner with
  the human's feedback attached, not a fresh analysis. The Phase 6/7
  deterministic workflow graph remains in the codebase, fully tested, as
  a deliberate side-by-side comparison (same relationship Phase 4/5's
  tool-calling agent has to Phase 6+).
- Phase 9: production infrastructure, added as a NEW, versioned `/api/v1`
  surface alongside the unchanged Phase 1-8 API (still at `/api/...`,
  unauthenticated, synchronous). PostgreSQL persistence via SQLAlchemy +
  Alembic migrations (`User`/`Dataset`/`Analysis`/`AnalysisMessage`/
  `AnalysisJob`); the LangGraph checkpointer becomes a persistent
  `AsyncPostgresSaver` when `DATABASE_URL` points at Postgres (falls back
  to `InMemorySaver` otherwise, unchanged from Phase 8); Redis for
  caching, rate limiting, and Celery's broker/backend; long-running
  analysis now runs as a background Celery job
  (`POST /api/v1/analysis` returns `{"analysis_id", "status": "queued"}`
  immediately) with real-time progress over Server-Sent Events
  (`GET /api/v1/analysis/{id}/stream`); JWT authentication + per-user
  ownership on datasets/analyses (404, never leaking another user's
  resource); Redis-backed, per-route, configurable rate limiting;
  request IDs and structured (JSON in non-dev) logging; centralized
  `{"error": {"code","message","request_id"}}` error responses; basic
  dataset versioning, idempotency keys, cooperative job cancellation, and
  analysis timeouts; `GET /health/live` + `GET /health/ready`; a
  `docker-compose.yml` (api/worker/postgres/redis, healthchecked).
- Phase 10: autonomous deep-analysis mode
  (`POST /api/v1/analysis` with `"mode": "deep"` — no specific question
  required). A separate, longer-running LangGraph graph: profiles the
  dataset and checks data quality (deterministic) → plans and generates
  its own hypotheses (LLM, grounded in the real profile) → tests them via
  trend/anomaly/correlation/segmentation analysis and hypothesis testing
  (deterministic dispatcher) → produces a narrative of insights with
  deterministic evidence/confidence scoring → a reviewer checks for
  contradictions and unsupported causal language, sending it back for
  correction up to `MAX_DEEP_ANALYSIS_REVIEW_RETRIES` times → a final
  report with chart recommendations. Bounded by `MAX_HYPOTHESES`,
  `MAX_INSIGHTS`, `MAX_DEEP_ANALYSIS_STEPS`, `MAX_DEEP_ANALYSIS_LLM_CALLS`,
  `MAX_DEEP_ANALYSIS_QUERY_EXECUTIONS`, and a wall-clock
  `MAX_DEEP_ANALYSIS_SECONDS` budget. Reuses the same LangGraph/
  structured-output patterns as Phases 6-8 rather than reinventing them;
  is a new, separate graph that doesn't touch or replace the Phase 8
  multi-agent Q&A graph.

See the project write-up for exactly what could and couldn't be
empirically verified without a local Docker/Postgres/Redis in the
original development environment — the DB layer is tested against
SQLite, Redis-dependent code against `fakeredis`, and Celery against its
own synchronous "eager" test mode; see also the write-up's "potential
bottlenecks" section for DuckDB's single-writer-process constraint under
separate api/worker containers.

</details>

<details>
<summary><strong>API quick reference (Phase 1-8, unauthenticated) — click to expand</strong></summary>

- Health check: `GET http://127.0.0.1:8000/api/health`
- Test agent: `POST http://127.0.0.1:8000/api/test-agent` with `{"message": "Hello"}`
- Upload a dataset: `POST http://127.0.0.1:8000/api/datasets/upload` (multipart, field `file`)
- List datasets: `GET http://127.0.0.1:8000/api/datasets`
- Dataset detail: `GET http://127.0.0.1:8000/api/datasets/{dataset_id}`
- Delete dataset: `DELETE http://127.0.0.1:8000/api/datasets/{dataset_id}`
- Run a query: `POST http://127.0.0.1:8000/api/analysis/query` with `{"dataset_id": "...", "query": "SELECT * FROM dataset LIMIT 10"}`
- Dataset SQL schema: `GET http://127.0.0.1:8000/api/analysis/{dataset_id}/schema`
- Dataset sample rows: `GET http://127.0.0.1:8000/api/analysis/{dataset_id}/sample`
- Ask the agent: `POST http://127.0.0.1:8000/api/analysis` with `{"dataset_id": "...", "question": "Which region has the highest revenue?"}` → returns either a validated `AnalysisResponse` (answer, key_findings, metrics, `sql_used` as a list, `chart`, `follow_up_suggestions`, `thread_id`, confidence) or, if the multi-agent graph paused for human approval, `{"status": "approval_required", "reason": "...", "thread_id": "..."}`. Add `"debug": true` for a `debug` field with safe execution metadata (plan, per-step SQL/results, retry count, current agent, reviewer result, approval status). Add `"thread_id": "<from a previous response>"` to ask a grounded follow-up question.
- Check a thread's status: `GET http://127.0.0.1:8000/api/analysis/{thread_id}/status`
- Approve or reject a paused analysis: `POST http://127.0.0.1:8000/api/analysis/{thread_id}/approve` with `{"approved": true}` or `{"approved": false, "feedback": "..."}`
- Interactive docs: `http://127.0.0.1:8000/docs`

Manual tool-calling demo (needs a real `OPENAI_API_KEY`, makes real OpenAI calls):

```bash
cd backend
python -m tests.manual_tool_call
```

Isolated structured-output tests (also need a real `OPENAI_API_KEY`; skipped otherwise):

```bash
cd backend
OPENAI_API_KEY=sk-... pytest tests/test_structured_output.py -v
```

Run the full test suite:

```bash
pytest
```

</details>

<details>
<summary><strong>The `/api/v1` surface (Phase 9+, authenticated) — click to expand</strong></summary>

Needs `DATABASE_URL` (Postgres in production) and `REDIS_URL` set to
persist data / run jobs in the background — with `.env` unchanged from
the Quickstart above (no `DATABASE_URL`/`REDIS_URL`), `/api/v1/...`
routes that need them return a clear error rather than silently falling
back to Phase 1-8 behavior. See
[Running the full stack locally, no Docker required](#running-the-full-stack-locally-no-docker-required)
above for the local SQLite/no-Redis fallback path.

```bash
# 1. Register + log in
curl -X POST :8000/api/v1/auth/register -H 'Content-Type: application/json' \
  -d '{"email": "you@example.com", "password": "supersecret123"}'
# -> {"access_token": "...", "token_type": "bearer", "expires_in_minutes": 60}

TOKEN=<access_token from above>

# 2. Upload a dataset (owned by you)
curl -X POST :8000/api/v1/datasets -H "Authorization: Bearer $TOKEN" -F file=@sales.csv

# 3. Create an analysis -- returns immediately, runs in the background
curl -X POST :8000/api/v1/analysis -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"dataset_id": "...", "question": "Which region has the highest revenue?"}'
# -> 202 {"analysis_id": "...", "status": "queued"}

# 4. Watch it live (needs real Redis -- see the no-Docker section above for the polling alternative)
curl -N :8000/api/v1/analysis/<analysis_id>/stream -H "Authorization: Bearer $TOKEN"

# 5. Or just poll
curl :8000/api/v1/analysis/<analysis_id> -H "Authorization: Bearer $TOKEN"

# If it pauses for human approval:
curl -X POST :8000/api/v1/analysis/<analysis_id>/approve -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"approved": true}'

# Autonomous deep-analysis mode instead of a specific question (Phase 10):
curl -X POST :8000/api/v1/analysis -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"dataset_id": "...", "mode": "deep"}'
```

</details>

### Docker (production-shaped: api + worker + postgres + redis)

```bash
cp .env.example .env    # fill in OPENAI_API_KEY
docker compose up --build
curl http://localhost:8000/health/ready
```

Real Postgres + Redis (`DATABASE_URL`/`REDIS_URL` pointing at them,
either via Docker or native local installs) give you real-time SSE
streaming, an actual background worker process, and behavior that
matches production; the SQLite/no-Redis path above is for quick local
iteration without standing up extra services.
