"""
Pydantic request/response models for the agent test endpoint.

FastAPI uses these for request validation, response serialization, and
OpenAPI schema generation. They live outside the routing module so the
data contract is reusable and testable independently of the endpoint.
"""

from pydantic import BaseModel, Field


class TestAgentRequest(BaseModel):
    """Request body for POST /api/test-agent."""

    message: str = Field(..., min_length=1, description="Message to send to the agent")


class TestAgentResponse(BaseModel):
    """Response body for POST /api/test-agent."""

    response: str
