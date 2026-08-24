"""
The Data Analyst agent: turns real SQL results into grounded findings.

Never sees the raw executed_queries directly -- it's given the typed
SQLAnalysisResult contract (see agent/nodes.py::summarize_sql_analysis,
shared with the Reviewer so both check the same summary) plus the
dataset schema. On a correction cycle (the Reviewer rejected the
previous attempt), it also receives that ReviewResult's issues/
corrections, per DATA_ANALYST_PROMPT's instruction to fix exactly those
problems rather than regenerate from scratch.
"""

import logging
from typing import Callable

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from app.agent.nodes import _format_schema_for_prompt, summarize_sql_analysis
from app.agent.prompts import DATA_ANALYST_PROMPT
from app.agent.state import MultiAgentState
from app.schemas.analysis import AnalyticalFindings

logger = logging.getLogger(__name__)


def build_data_analyst_node(structured_model: Runnable) -> Callable[[MultiAgentState], dict]:
    """Build the 'data_analyst' node.

    Runs once per correction cycle (up to Settings.max_review_retries + 1
    times total): the first pass right after the SQL Analyst subgraph
    finishes, and again each time route_after_reviewer sends it back with
    a rejected ReviewResult still in state.
    """

    def data_analyst_node(state: MultiAgentState) -> dict:
        sql_result = summarize_sql_analysis(state)
        schema = state.get("dataset_schema")

        content_parts = [
            f"User question: {state['user_question']}",
            "Dataset schema (only these columns exist -- never reference any others):\n"
            f"{_format_schema_for_prompt(schema) if schema else '(unknown)'}",
            f"SQL analysis result:\n{sql_result.model_dump_json()}",
        ]

        review = state.get("review")
        if review is not None and not review.approved:
            content_parts.append(
                "The Reviewer rejected your previous attempt. Fix exactly these problems:\n"
                f"Issues: {review.issues}\n"
                f"Corrections: {review.corrections}"
            )

        messages: list[BaseMessage] = [
            SystemMessage(content=DATA_ANALYST_PROMPT),
            HumanMessage(content="\n\n".join(content_parts)),
        ]

        findings: AnalyticalFindings = structured_model.invoke(messages)

        logger.info(
            "Data Analyst produced findings (dataset_id=%s, confidence=%.2f, retry_cycle=%d)",
            state.get("dataset_id"),
            findings.confidence,
            state.get("review_retry_count", 0),
        )

        return {"current_agent": "data_analyst", "findings": findings}

    return data_analyst_node
