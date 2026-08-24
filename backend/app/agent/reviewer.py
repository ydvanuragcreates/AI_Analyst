"""
The Reviewer agent + the human-in-the-loop approval gate.

Both live in this module because they're two halves of the same quality
gate: the Reviewer is the automated check, human approval is the escalation
path when the automated check isn't enough to trust on its own (low
confidence, an expensive analysis, or repeated disagreement). Kept
separate from agent/data_analyst.py because the Reviewer's job -- judging
someone else's output, not producing its own analysis -- is a distinct
responsibility, matching the "5 named agents" architectural rule.
"""

import logging
from typing import Callable

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langgraph.types import interrupt

from app.agent.nodes import _format_schema_for_prompt, summarize_sql_analysis
from app.agent.prompts import REVIEWER_PROMPT
from app.agent.state import MultiAgentState
from app.core.config import get_settings
from app.schemas.review import ReviewResult

logger = logging.getLogger(__name__)


def build_reviewer_node(structured_model: Runnable) -> Callable[[MultiAgentState], dict]:
    """Build the 'reviewer' node.

    Checks the Data Analyst's AnalyticalFindings against the SAME
    SQLAnalysisResult summary the Data Analyst was given (see
    agent/nodes.py::summarize_sql_analysis) -- not a second independent
    analysis, a check of this specific one.

    review_retry_count is incremented here, only on rejection -- it's the
    count of correction cycles *consumed so far*, which is exactly what
    route_after_reviewer and _requires_human_approval below need to
    decide whether another automated correction attempt is still allowed.
    """

    def reviewer_node(state: MultiAgentState) -> dict:
        sql_result = summarize_sql_analysis(state)
        schema = state.get("dataset_schema")
        findings = state["findings"]

        content = (
            f"User question: {state['user_question']}\n\n"
            "Dataset schema:\n"
            f"{_format_schema_for_prompt(schema) if schema else '(unknown)'}\n\n"
            f"Actual SQL query results:\n{sql_result.model_dump_json()}\n\n"
            f"Data Analyst's findings to check:\n{findings.model_dump_json()}"
        )
        messages: list[BaseMessage] = [SystemMessage(content=REVIEWER_PROMPT), HumanMessage(content=content)]

        review: ReviewResult = structured_model.invoke(messages)

        retry_count = state.get("review_retry_count", 0)
        if not review.approved:
            retry_count += 1

        logger.info(
            "Reviewer judged findings (dataset_id=%s): approved=%s confidence=%.2f retry_cycle=%d",
            state.get("dataset_id"),
            review.approved,
            review.confidence,
            retry_count,
        )

        return {"current_agent": "reviewer", "review": review, "review_retry_count": retry_count}

    return reviewer_node


def _requires_human_approval(state: MultiAgentState) -> tuple[bool, str]:
    """Deterministic (no LLM call) check for whether a human must approve
    before the final response can be produced.

    Checked in order; the first true condition's reason is what's shown
    to the human and returned in ApprovalRequiredResponse.reason:
    1. low confidence -- the Reviewer's own confidence, per Settings.human_approval_confidence_threshold.
    2. expensive analysis -- total execution time across every query that
       actually ran, per Settings.human_approval_cost_threshold_ms. Kept
       this simple deliberately -- no token/dollar cost model, just query
       execution time, per the spec's "keep the cost mechanism simple."
    3. repeated reviewer disagreement -- the automated correction budget
       (Settings.max_review_retries) is exhausted and the Reviewer still
       has not approved.
    """

    settings = get_settings()
    review = state.get("review")

    if review is not None and review.confidence < settings.human_approval_confidence_threshold:
        return True, f"Reviewer confidence ({review.confidence:.2f}) is below the approval threshold."

    total_ms = sum(query.execution_time_ms for query in state.get("executed_queries", []))
    if total_ms > settings.human_approval_cost_threshold_ms:
        return True, f"Analysis took {total_ms:.0f}ms, above the cost threshold for automatic approval."

    if review is not None and not review.approved and state.get("review_retry_count", 0) >= settings.max_review_retries:
        return True, "The Reviewer and Data Analyst could not reach agreement after the maximum correction attempts."

    return False, ""


def route_after_reviewer(state: MultiAgentState) -> str:
    """Decide what happens after the Reviewer judges the current findings.

    - Rejected AND correction budget remains: back to the Data Analyst
      for another attempt (review/review_retry_count already reflect this
      rejection, consumed by build_data_analyst_node's correction-cycle
      context).
    - Otherwise (approved, or rejected with the budget exhausted): check
      whether a human needs to sign off before the final response --
      either because this specific approved result still trips the
      confidence/cost triggers, or because the retries ran out without
      agreement.
    """

    settings = get_settings()
    review = state["review"]

    if not review.approved and state.get("review_retry_count", 0) < settings.max_review_retries:
        return "data_analyst"

    requires_approval, _ = _requires_human_approval(state)
    if requires_approval:
        return "human_approval"
    return "generate_final_response"


def build_human_approval_node() -> Callable[[MultiAgentState], dict]:
    """Build the 'human_approval' node: pause the graph and wait for a human.

    Calls LangGraph's interrupt() with a payload describing why approval
    is needed -- the graph run stops here (state is checkpointed by the
    parent's checkpointer) until something resumes it with
    Command(resume={"approved": ..., "feedback": ...}) via
    POST /api/analysis/{thread_id}/approve. On resume, this node re-runs
    from the top and interrupt() returns that resume value directly (this
    was verified empirically before writing this module).
    """

    def human_approval_node(state: MultiAgentState) -> dict:
        _, reason = _requires_human_approval(state)
        reason = reason or "Human approval required."

        payload = {
            "reason": reason,
            "user_question": state["user_question"],
            "findings": state["findings"].model_dump() if state.get("findings") else None,
            "review": state["review"].model_dump() if state.get("review") else None,
        }
        logger.info("Human approval requested (dataset_id=%s): %s", state.get("dataset_id"), reason)

        resume_value = interrupt(payload)

        approved = bool(resume_value.get("approved")) if isinstance(resume_value, dict) else bool(resume_value)
        feedback = resume_value.get("feedback") if isinstance(resume_value, dict) else None

        logger.info("Human approval resumed (dataset_id=%s): approved=%s", state.get("dataset_id"), approved)

        update: dict = {
            "current_agent": "human_approval",
            "human_approval_required": True,
            "human_approval_reason": reason,
            "human_approved": approved,
            "human_feedback": feedback,
        }
        if not approved:
            # Rejection restarts the correction budget and reuses
            # `retry_reason` -- already read by the Planner
            # (build_create_analysis_plan_node) as "why the previous
            # attempt didn't work" -- to carry the human's own feedback
            # into the re-plan, rather than adding a parallel field the
            # Planner would also need to know to check.
            update["review_retry_count"] = 0
            update["retry_reason"] = feedback or "The human reviewer rejected the previous analysis."

        return update

    return human_approval_node


def route_after_human_approval(state: MultiAgentState) -> str:
    """Decide what happens after a human responds.

    Approved: straight to the final response. Rejected: back to the
    Planner WITH the human's feedback still in state (human_feedback is
    never cleared here) so it flows Human Feedback -> Planner -> SQL
    Analyst -> Data Analyst -> Reviewer, per the spec's explicit "do not
    discard the previous context" instruction -- not a fresh analysis
    from scratch.
    """

    if state.get("human_approved"):
        return "generate_final_response"
    return "planner"
