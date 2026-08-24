"""
Smoke test for the health endpoint.

Uses FastAPI's TestClient, which runs the app in-process (no network, no
real OpenAI calls), so this test is safe to run without an API key... except
that `main.py` calls `get_settings()` at import time, which requires
OPENAI_API_KEY to be set. Run tests with a dummy value if needed, e.g.:

    OPENAI_API_KEY=test-key pytest
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check_returns_ok() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_allows_configured_frontend_origin() -> None:
    """Regression test for the Phase 10 frontend's CORS requirement (see
    main.py's CORSMiddleware / Settings.cors_allowed_origins) -- a
    browser preflight from the default Next.js dev origin must be
    allowed, not silently blocked."""

    response = client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_rejects_unconfigured_origin() -> None:
    response = client.options(
        "/api/health",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert "access-control-allow-origin" not in response.headers


def test_cors_allowed_origins_list_splits_comma_separated_value() -> None:
    """Settings.cors_allowed_origins is a plain comma-separated string
    (never list[str] -- see config.py's field docstring for why a
    list-typed field crashes on the natural '.env' shape), split into a
    list via the cors_allowed_origins_list property."""

    from app.core.config import Settings

    settings = Settings(
        openai_api_key="test-key",
        cors_allowed_origins="http://localhost:3000, https://app.example.com",
    )
    assert settings.cors_allowed_origins_list == ["http://localhost:3000", "https://app.example.com"]


def test_cors_allowed_origins_from_real_env_var_does_not_crash_settings() -> None:
    """Regression test for the exact failure this environment-variable
    shape caused before cors_allowed_origins was changed from list[str]
    to str: pydantic-settings raised SettingsError trying to JSON-decode
    a plain comma-separated value as a list, before any validator could
    intervene. Only an env var (not a constructor kwarg) exercises
    pydantic-settings' EnvSettingsSource parsing path -- a kwarg bypasses
    it entirely, which is exactly how the original bug went undetected."""

    import os

    from app.core.config import Settings, get_settings

    previous = os.environ.get("CORS_ALLOWED_ORIGINS")
    os.environ["CORS_ALLOWED_ORIGINS"] = "http://localhost:3000,https://app.example.com"
    try:
        settings = Settings(openai_api_key="test-key")
        assert settings.cors_allowed_origins_list == ["http://localhost:3000", "https://app.example.com"]
    finally:
        if previous is None:
            os.environ.pop("CORS_ALLOWED_ORIGINS", None)
        else:
            os.environ["CORS_ALLOWED_ORIGINS"] = previous
        get_settings.cache_clear()
