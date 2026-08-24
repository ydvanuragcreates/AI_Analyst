"""
Centralized OpenAI chat model factory.

This is the ONLY place in the application that constructs a chat model.
Every other module (LangGraph nodes, future services) must call
`get_chat_model()` instead of importing `ChatOpenAI` directly. That means the
model name/provider can change later (env var, or even a different provider)
without touching any agent code.
"""

from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.core.config import get_settings


@lru_cache
def get_chat_model() -> BaseChatModel:
    """
    Build (and cache) the chat model used throughout the application.

    Uses LangChain's `init_chat_model()` so the underlying provider/model is
    configuration-driven rather than hardcoded. `model_provider="openai"` is
    passed explicitly rather than relying on inference from the model name,
    since OPENAI_MODEL is an arbitrary, env-controlled string.
    """

    settings = get_settings()
    return init_chat_model(
        model=settings.openai_model,
        model_provider="openai",
        api_key=settings.openai_api_key,
        temperature=settings.openai_temperature,
        max_tokens=settings.openai_max_output_tokens,
    )
