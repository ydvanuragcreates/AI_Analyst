"""
Agent test endpoint.

Exposes the Phase 1 LangGraph workflow over HTTP: the request body is
validated by `TestAgentRequest`, fed into the compiled graph as the initial
`AnalystState`, and the resulting `response` field is returned.

Not named `analysis.py` (per the eventual project structure) because this
endpoint is a throwaway smoke test for the LangChain/LangGraph wiring, not
the real dataset-analysis workflow that will live under `analysis.py` in a
later phase.
"""

import logging

from fastapi import APIRouter, HTTPException
from openai import OpenAIError

from app.agent.graph import get_agent_graph
from app.schemas.agent import TestAgentRequest, TestAgentResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/test-agent", response_model=TestAgentResponse)
async def test_agent(payload: TestAgentRequest) -> TestAgentResponse:
    """Run the user's message through the LangGraph agent workflow."""

    graph = get_agent_graph()

    try:
        result = await graph.ainvoke({"user_question": payload.message, "response": ""})
    except OpenAIError:
        logger.exception("OpenAI API error while handling /test-agent request")
        raise HTTPException(
            status_code=502,
            detail="The AI service is currently unavailable. Please try again later.",
        )
    except Exception:
        logger.exception("Unexpected error while handling /test-agent request")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred while processing your request.",
        )

    return TestAgentResponse(response=result["response"])
