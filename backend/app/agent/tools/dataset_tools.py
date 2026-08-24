"""
LangChain tools for dataset schema/sample inspection.

Thin wrappers only, as instructed: all real logic lives in DuckDBService
(Phase 3). A tool's job is to (1) describe itself to the model via its
docstring/type hints, (2) translate the model's request into a
DuckDBService call, and (3) turn the typed Pydantic result into a plain
JSON-serializable dict. No DuckDB/SQL logic is duplicated here.

`dataset_id` is declared with `InjectedState`, not as a normal argument.
That means it never appears in the JSON schema sent to OpenAI -- the
model cannot see it, request it, or get it wrong. LangGraph fills it in
automatically from the graph's `dataset_id` state field when the tool
actually runs (see agent/graph.py). This is the "cleaner state/context
mechanism" for passing the dataset ID through the workflow instead of
relying on the model to remember/repeat an ID, or stuffing it into the
system prompt on every turn.
"""

import logging
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.core.config import get_settings
from app.services.duckdb_service import get_duckdb_service
from app.services.exceptions import DatasetServiceError

logger = logging.getLogger(__name__)


@tool
def get_dataset_schema(dataset_id: Annotated[str, InjectedState("dataset_id")]) -> dict[str, Any]:
    """Get the column names and SQL types for the current dataset.

    Call this first if you are not already sure what columns are
    available before writing a SQL query.
    """

    try:
        schema = get_duckdb_service().get_schema(dataset_id)
    except DatasetServiceError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Unexpected error in get_dataset_schema for dataset %s", dataset_id)
        return {"error": "An unexpected error occurred while reading the dataset schema."}

    return schema.model_dump()


@tool
def get_sample_rows(
    dataset_id: Annotated[str, InjectedState("dataset_id")],
    limit: int = 5,
) -> dict[str, Any]:
    """Return a small sample of rows from the current dataset.

    Useful for seeing real example values (formats, typical strings,
    date shapes) before writing SQL. `limit` is capped at the
    application's configured maximum preview size regardless of what is
    requested.
    """

    settings = get_settings()
    bounded_limit = max(1, min(limit, settings.dataset_preview_rows))

    try:
        sample = get_duckdb_service().get_sample(dataset_id, rows=bounded_limit)
    except DatasetServiceError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Unexpected error in get_sample_rows for dataset %s", dataset_id)
        return {"error": "An unexpected error occurred while sampling the dataset."}

    return sample.model_dump()
