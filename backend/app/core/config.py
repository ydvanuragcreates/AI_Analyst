"""
Centralized application configuration.

Every setting the app needs (API keys, model name, DB URL, environment) is
declared once here as a typed Pydantic model, instead of being read from
`os.environ` scattered across the codebase. This is the single source of
truth other modules depend on.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents[3] is the project root (d:/ai_knowledge),
# where the shared .env file lives (see project structure in README.md).
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Typed, validated application settings loaded from environment variables."""

    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    cors_allowed_origins: str = Field(
        default="http://localhost:3000",
        description="Comma-separated origins the browser-based frontend is served from (Next.js dev server by "
        "default), e.g. 'https://app.example.com,https://staging.example.com'. Required for the frontend (a "
        "separate origin) to call this API at all -- see main.py's CORSMiddleware and the "
        "`cors_allowed_origins_list` property below.  Deliberately typed as a plain `str` here, not `list[str]`: "
        "pydantic-settings parses any list/dict-typed field read from an env var as JSON *before* any field "
        "validator on it ever runs (confirmed empirically -- a validator cannot intervene early enough to accept "
        "a plain comma-separated string instead), so a `list[str]` field would reject exactly the natural "
        "'CORS_ALLOWED_ORIGINS=http://localhost:3000' .env syntax with a startup-crashing SettingsError.",
    )

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    llm_provider: Literal["openai", "ollama"] = Field(
        default="openai",
        description="Which LLM backend every agent uses. 'ollama' = a free local model for "
        "development/testing; 'openai' = the paid hosted model. Switching needs only this env "
        "var (plus that provider's settings below) -- no agent code changes.",
    )

    openai_api_key: str | None = Field(default=None, description="Required when LLM_PROVIDER=openai")
    openai_model: str = Field(default="gpt-5.4-mini")
    openai_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    openai_max_output_tokens: int = Field(
        default=4096, description="Caps a single LLM call's output -- without this, nothing bounds how many "
        "tokens one agent call (or a runaway/looping model reply) can generate"
    )

    ollama_base_url: str = Field(
        default="http://localhost:11434/v1", description="Ollama's OpenAI-compatible endpoint (note the /v1)"
    )
    ollama_model: str = Field(default="qwen2.5:3b", description="Any model pulled with `ollama pull`")
    llm_structured_output_retries: int = Field(
        default=2, ge=0, le=5,
        description="Local models only: how many times a reply that fails schema validation "
        "(e.g. confidence=100 where the schema says 0-1) is sent back to the model with the "
        "validation error to correct. Hosted OpenAI models are not wrapped.",
    )

    @model_validator(mode="after")
    def _require_provider_credentials(self) -> "Settings":
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai (or set LLM_PROVIDER=ollama for a local model)")
        return self

    database_url: str | None = Field(
        default=None,
        description="SQLAlchemy URL for application persistence (Postgres in production, e.g. "
        "'postgresql+psycopg://user:pass@host/db'; a sqlite+aiosqlite URL is used in tests). "
        "None means 'no database configured' -- the LangGraph checkpointer falls back to "
        "InMemorySaver and the Phase 9 v1 endpoints that require it are unavailable.",
    )
    db_pool_size: int = Field(default=5)
    db_max_overflow: int = Field(default=10)
    db_pool_timeout: int = Field(default=30)
    daily_analysis_limit: int = Field(
        default=0,
        description="Max analyses created per UTC day across ALL users (0 = unlimited). Protects the LLM "
        "budget on a public demo. Counted from the analyses table, so it survives restarts.",
    )
    daily_analysis_limit_per_client: int = Field(
        default=0, description="Max analyses per visitor (hashed IP) per UTC day (0 = unlimited)."
    )
    daily_token_budget: int = Field(
        default=0,
        description="Stop accepting new analyses once today's recorded LLM tokens (all users) reach this "
        "(0 = unlimited). Caps cost, not count: deep analyses use several times more tokens.",
    )
    daily_upload_limit_per_client: int = Field(
        default=0, description="Max dataset uploads per visitor (hashed IP) per UTC day (0 = unlimited)."
    )
    client_ip_header: str = Field(
        default="",
        description="Header the hosting platform's edge sets to the real client IP (e.g. 'true-client-ip'). "
        "Empty = not used. See core/client_ip.py.",
    )
    trusted_proxy_hops: int = Field(
        default=0,
        description="Number of our own proxies appending to X-Forwarded-For; the client IP is that many "
        "entries from the right. 0 = not used. See core/client_ip.py.",
    )
    dataset_retention_days: int = Field(
        default=0,
        description="Delete datasets (and their analyses) older than this many days at startup "
        "(0 = keep forever). See services/retention.py.",
    )
    enable_legacy_api: bool = Field(
        default=True,
        description="Mount the unversioned Phase 1-8 routes (/api/agent, /api/datasets, /api/analysis). "
        "They call the LLM synchronously and bypass the v1 limits; the web app only uses /api/v1.",
    )
    db_disable_pooling: bool = Field(
        default=False,
        description="Open a fresh DB connection per session instead of pooling. Required when the "
        "Celery worker runs inside the API process (app/scripts/serve.py): pooled async connections "
        "belong to the event loop that opened them, and worker tasks each run on their own loop.",
    )

    redis_url: str | None = Field(
        default=None,
        description="Redis connection URL, e.g. 'redis://localhost:6379/0'. Used for caching, "
        "rate limiting, job coordination (Celery broker/backend), and SSE pub/sub. None disables "
        "all of those (rate limiting/caching become no-ops; background jobs are unavailable).",
    )
    cache_ttl_seconds: int = Field(default=300, description="Default TTL for cached values (e.g. dataset schema)")

    analysis_rate_limit: str = Field(
        default="10/minute", description="Format '<count>/<window>', window one of second|minute|hour"
    )
    upload_rate_limit: str = Field(default="20/minute")

    analysis_timeout_seconds: int = Field(default=120, description="Hard ceiling on one analysis job's execution time")
    job_max_retries: int = Field(default=3, description="Bounded retries for transient (not permanent) job failures")

    max_question_length: int = Field(default=5000)
    default_page_size: int = Field(default=20)
    max_page_size: int = Field(default=100)
    idempotency_key_ttl_seconds: int = Field(
        default=86400,
        description="How long an Idempotency-Key keeps replaying its original analysis before a reused key "
        "is treated as a new request instead (see api/routes/analysis_jobs.py)",
    )

    max_file_size_mb: int = Field(default=50)
    dataset_storage_path: str = Field(default="./storage/datasets")
    dataset_preview_rows: int = Field(default=5)

    duckdb_path: str = Field(default="./storage/duckdb/analytics.duckdb")
    max_query_rows: int = Field(default=1000)
    max_query_execution_seconds: float = Field(default=10)
    duckdb_lock_timeout_seconds: float = Field(
        default=30.0,
        description="Max wait for the cross-process DuckDB file lock (see services/duckdb_service.py's "
        "concurrency note) before failing with a 'busy, retry' error instead of blocking forever",
    )
    max_dataset_rows: int = Field(default=1_000_000, description="Reject an uploaded dataset with more rows than this")
    max_dataset_columns: int = Field(default=500, description="Reject an uploaded dataset with more columns than this")

    max_sql_retries: int = Field(
        default=3, description="Maximum SQL generation attempts per analysis step before giving up on that step"
    )
    max_analysis_steps: int = Field(
        default=5, description="Maximum steps an analysis plan may contain; longer plans are capped, not rejected"
    )

    max_review_retries: int = Field(
        default=2, description="Maximum Data Analyst <-> Reviewer correction cycles before escalating to a human"
    )
    human_approval_confidence_threshold: float = Field(
        default=0.70,
        description="Reviewer confidence below this triggers a human-approval interrupt (see 'APPROVAL THRESHOLD')",
    )
    human_approval_cost_threshold_ms: float = Field(
        default=2000.0,
        description="Total SQL execution time above this (a simple proxy for 'expensive analysis') triggers a "
        "human-approval interrupt -- deliberately not a real billing/cost model, per the Phase 8 spec",
    )

    # ------------------------------------------------------------------ #
    # Phase 10: autonomous deep-analysis pipeline (agent/deep_analysis/).
    # Separate knobs from the Phase 6-8 Q&A workflow's above -- a deep
    # analysis run genuinely does more work (profiling + quality +
    # multiple hypotheses + trend/anomaly/correlation/segmentation +
    # insight review), so it gets its own, larger budget rather than
    # silently sharing (and quietly changing the meaning of) the Q&A
    # graph's existing limits.
    # ------------------------------------------------------------------ #
    max_hypotheses: int = Field(default=5, description="Maximum hypotheses the Hypothesis Generator may produce per run")
    max_insights: int = Field(default=6, description="Maximum insights returned after prioritization (section 14)")
    max_deep_analysis_review_retries: int = Field(
        default=2, description="Maximum Insight Engine <-> Reviewer correction cycles before finalizing with a caveat"
    )
    max_deep_analysis_steps: int = Field(
        default=12, description="Section 27 budget: max total deterministic analysis steps (profile/trend/anomaly/"
        "correlation/segmentation/hypothesis-test) in one run"
    )
    max_deep_analysis_llm_calls: int = Field(
        default=10, description="Section 27 budget: max LLM calls (planner, hypothesis generator, insight writer, "
        "reviewer, report writer) in one run"
    )
    max_deep_analysis_query_executions: int = Field(
        default=25, description="Section 27 budget: max DuckDB queries (profiling + quality + per-hypothesis "
        "computation) in one run"
    )
    max_deep_analysis_seconds: float = Field(
        default=180.0, description="Section 27 budget: wall-clock ceiling for one deep-analysis run"
    )
    high_cardinality_ratio_threshold: float = Field(
        default=0.9, description="A column is 'high cardinality' when distinct/row_count exceeds this (and "
        "distinct_count is large enough to matter -- see profiling_service)"
    )
    outlier_iqr_multiplier: float = Field(default=1.5, description="Standard Tukey IQR fence multiplier for outlier/anomaly detection")
    correlation_min_sample_size: int = Field(default=5, description="Minimum non-null paired rows required before a correlation is reported at all")

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def dataset_storage_root(self) -> Path:
        """Absolute path to the dataset storage directory.

        A relative DATASET_STORAGE_PATH is resolved against the project
        root rather than the process's current working directory, so
        uploads land in the same place regardless of where uvicorn is
        launched from.
        """

        path = Path(self.dataset_storage_path)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def duckdb_database_path(self) -> Path:
        """Absolute path to the DuckDB database file (same resolution rule as above)."""

        path = Path(self.duckdb_path)
        return path if path.is_absolute() else PROJECT_ROOT / path


@lru_cache
def get_settings() -> Settings:
    """
    Return the cached Settings instance.

    `lru_cache` gives us a process-wide singleton without a mutable global
    variable: the environment is only read and validated once, and every
    caller (config, logging, the model factory, FastAPI dependencies) shares
    the same instance. If required variables (e.g. OPENAI_API_KEY) are
    missing, Pydantic raises a clear ValidationError immediately here,
    instead of failing later deep inside an OpenAI call.
    """

    return Settings()
