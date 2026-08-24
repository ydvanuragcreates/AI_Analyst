"""
The Supervisor agent: routes work, never performs analysis itself.

A single structured-output call producing a SupervisorDecision (see
schemas/analysis.py), which the graph's conditional edges read to decide
whether the Planner runs at all (see agent/graph.py::route_after_supervisor
and build_trivial_plan_node in agent/nodes.py for the deterministic
1-step-plan shortcut taken when requires_planning is False).
"""

import logging
from typing import Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from app.agent.prompts import SUPERVISOR_PROMPT
from app.agent.state import MultiAgentState
from app.schemas.analysis import SupervisorDecision

logger = logging.getLogger(__name__)


def build_supervisor_node(structured_model: Runnable) -> Callable[[MultiAgentState], dict]:
    """Build the 'supervisor' node.

    Runs first, before the dataset schema is even inspected -- like
    Phase 7's analyze_question, this is triage on the question's own
    wording (plus prior-turn conversation context, if any), not a
    grounded plan. It never sees query results or dataset rows; it only
    ever decides *where to route*.
    """

    def supervisor_node(state: MultiAgentState) -> dict:
        messages = [SystemMessage(content=SUPERVISOR_PROMPT)]

        context = state.get("conversation_context")
        if context and context.previous_question:
            messages.append(
                SystemMessage(
                    content=(
                        "Context from the previous turn in this conversation:\n"
                        f"Previous question: {context.previous_question}\n"
                        f"Previous answer: {context.previous_answer}\n\n"
                        "Use this only if the current question actually reads as a follow-up."
                    )
                )
            )
        messages.append(HumanMessage(content=state["user_question"]))

        decision: SupervisorDecision = structured_model.invoke(messages)

        logger.info(
            "Supervisor routed (dataset_id=%s): complexity=%s planning=%s data_analysis=%s answerable=%s",
            state.get("dataset_id"),
            decision.complexity,
            decision.requires_planning,
            decision.requires_data_analysis,
            decision.is_answerable,
        )

        return {"current_agent": "supervisor", "supervisor_decision": decision}

    return supervisor_node
