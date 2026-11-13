"""
Centralized chat model factory.

This is the ONLY place in the application that constructs a chat model.
Every other module (LangGraph nodes, future services) must call
`get_chat_model()` instead of importing `ChatOpenAI` directly. That is what
lets the backend switch between a free local model (development/testing)
and the paid hosted model (production) with one env var, LLM_PROVIDER,
without touching any agent code:

- LLM_PROVIDER=openai -> OpenAI (OPENAI_API_KEY, OPENAI_MODEL)
- LLM_PROVIDER=ollama -> a local Ollama model (OLLAMA_MODEL, OLLAMA_BASE_URL)

Ollama is reached through its OpenAI-compatible API, so it uses the same
client library. Errors therefore stay `openai.*` exceptions, and the
existing OpenAIError handling in the routes and the worker's retry rules
apply unchanged.
"""

import json
from functools import lru_cache
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings


class LocalChatOpenAI(ChatOpenAI):
    """ChatOpenAI tuned for small local models served by Ollama.

    Two differences from the hosted model, both found by probing
    qwen2.5:3b with this app's real schemas:

    1. Structured output defaults to `json_schema` instead of
       `function_calling`. Small models often answer in plain text rather
       than calling the tool, which yields `None`; with `json_schema`,
       Ollama constrains decoding to the schema, so the reply is always
       well-formed JSON.
    2. Ollama never shows the schema to the model (it only constrains
       decoding), so field descriptions and numeric ranges are added to
       the prompt; and since decoding doesn't enforce numeric bounds, a
       reply that still fails Pydantic validation (e.g. `confidence: 100`
       for a 0-1 field) is sent back with the exact error, up to
       `structured_output_retries` times.

    The override lives on the model class (not in the call sites) because
    LangGraph's prebuilt ReAct agent calls `with_structured_output()`
    internally.
    """

    structured_output_retries: int = 2

    def with_structured_output(
        self,
        schema: Any = None,
        *,
        method: str = "json_schema",
        include_raw: bool = False,
        strict: bool | None = None,
        **kwargs: Any,
    ) -> Runnable:
        structured = super().with_structured_output(schema, method=method, include_raw=include_raw, strict=strict, **kwargs)
        # Only Pydantic schemas are wrapped; include_raw callers get
        # `parsing_error` back instead of an exception, so they can't be
        # retried here and are left as is.
        if include_raw or not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            return structured
        return _with_schema_guidance(self, structured, schema, self.structured_output_retries)


def _with_schema_guidance(model: BaseChatModel, structured: Runnable, schema: type[BaseModel], retries: int) -> Runnable:
    """Show the model the schema, then validate-and-retry.

    Ollama uses the JSON schema only to constrain decoding; unlike OpenAI
    it never shows the schema (field descriptions, numeric ranges) to the
    model. Without it a small model can't know e.g. that `confidence` is
    0-1, so the schema is added to the prompt here.
    """

    guidance = (
        "Respond ONLY with a JSON object that conforms to this JSON schema. Respect every field's "
        f"description, type and numeric range:\n{json.dumps(schema.model_json_schema())}"
    )

    def _messages(input: Any) -> list:
        messages = list(model._convert_input(input).to_messages())
        if messages and isinstance(messages[0], SystemMessage) and isinstance(messages[0].content, str):
            messages[0] = SystemMessage(content=f"{messages[0].content}\n\n{guidance}")
        else:
            messages.insert(0, SystemMessage(content=guidance))
        return messages

    def _correction(messages: list, error: Exception) -> list:
        return [
            *messages,
            HumanMessage(
                content=f"Your previous reply did not match the required JSON schema:\n{error}\n"
                "Reply again with JSON that satisfies every field's type and constraints exactly."
            ),
        ]

    def _invoke(input: Any, config: RunnableConfig) -> Any:
        base = _messages(input)
        attempt_input = base
        for attempt in range(retries + 1):
            try:
                return structured.invoke(attempt_input, config)
            except (ValidationError, OutputParserException) as exc:
                if attempt == retries:
                    raise
                attempt_input = _correction(base, exc)

    async def _ainvoke(input: Any, config: RunnableConfig) -> Any:
        base = _messages(input)
        attempt_input = base
        for attempt in range(retries + 1):
            try:
                return await structured.ainvoke(attempt_input, config)
            except (ValidationError, OutputParserException) as exc:
                if attempt == retries:
                    raise
                attempt_input = _correction(base, exc)

    return RunnableLambda(_invoke, afunc=_ainvoke, name="structured_output_with_validation_retry")


@lru_cache
def get_chat_model() -> BaseChatModel:
    """Build (and cache) the chat model used throughout the application."""

    settings = get_settings()

    if settings.llm_provider == "ollama":
        return LocalChatOpenAI(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            api_key="ollama",  # Ollama ignores it, but the OpenAI client requires one
            temperature=settings.openai_temperature,
            max_tokens=settings.openai_max_output_tokens,
            structured_output_retries=settings.llm_structured_output_retries,
        )

    # `model_provider="openai"` is explicit rather than inferred from the
    # model name, since OPENAI_MODEL is an arbitrary, env-controlled string.
    return init_chat_model(
        model=settings.openai_model,
        model_provider="openai",
        api_key=settings.openai_api_key,
        temperature=settings.openai_temperature,
        max_tokens=settings.openai_max_output_tokens,
    )
