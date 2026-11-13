"""LLM provider switch (LLM_PROVIDER=openai|ollama) and the local-model
structured-output wrapper. No network: nothing here calls a real model."""

import pytest
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field, ValidationError

from app.core import llm
from app.core.config import Settings, get_settings


class _Verdict(BaseModel):
    confidence: float = Field(ge=0, le=1, description="0 to 1")


def _local_model(retries: int = 2) -> llm.LocalChatOpenAI:
    return llm.LocalChatOpenAI(model="test", base_url="http://localhost:1/v1", api_key="x", structured_output_retries=retries)


def test_openai_provider_requires_api_key() -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY is required"):
        Settings(llm_provider="openai", openai_api_key=None, _env_file=None)


def test_ollama_provider_needs_no_api_key() -> None:
    settings = Settings(llm_provider="ollama", openai_api_key=None, _env_file=None)
    assert settings.ollama_model == "qwen2.5:3b"


@pytest.mark.parametrize("provider, expected_local", [("ollama", True), ("openai", False)])
def test_get_chat_model_follows_provider(monkeypatch, provider, expected_local) -> None:
    settings = Settings(llm_provider=provider, openai_api_key="test-key", _env_file=None)
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    llm.get_chat_model.cache_clear()
    try:
        assert isinstance(llm.get_chat_model(), llm.LocalChatOpenAI) is expected_local
    finally:
        llm.get_chat_model.cache_clear()
        get_settings.cache_clear()


def _run_wrapper(retries: int, replies: list):
    """Wrap a fake structured runnable that returns/raises `replies` in order."""

    seen_inputs: list = []

    def fake_structured(messages):
        seen_inputs.append(messages)
        reply = replies[len(seen_inputs) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply

    wrapped = llm._with_schema_guidance(_local_model(retries), RunnableLambda(fake_structured), _Verdict, retries)
    return wrapped, seen_inputs


def _validation_error() -> ValidationError:
    try:
        _Verdict(confidence=100)
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


def test_schema_is_shown_to_the_model() -> None:
    wrapped, seen = _run_wrapper(2, [_Verdict(confidence=0.5)])
    wrapped.invoke("Is this enough?")

    first = seen[0][0]
    assert isinstance(first, SystemMessage)
    assert '"maximum": 1' in first.content and "0 to 1" in first.content


def test_existing_system_prompt_is_kept_and_extended() -> None:
    wrapped, seen = _run_wrapper(2, [_Verdict(confidence=0.5)])
    wrapped.invoke([("system", "You are an analyst."), ("human", "Is this enough?")])

    system = seen[0][0]
    assert system.content.startswith("You are an analyst.") and "JSON schema" in system.content
    assert sum(isinstance(m, SystemMessage) for m in seen[0]) == 1


def test_invalid_reply_is_retried_with_the_error() -> None:
    wrapped, seen = _run_wrapper(2, [_validation_error(), _Verdict(confidence=0.9)])
    assert wrapped.invoke("Is this enough?").confidence == 0.9

    assert len(seen) == 2
    assert "less than or equal to 1" in seen[1][-1].content


async def test_retries_also_apply_to_async_calls() -> None:
    wrapped, seen = _run_wrapper(1, [_validation_error(), _Verdict(confidence=0.2)])
    assert (await wrapped.ainvoke("Is this enough?")).confidence == 0.2
    assert len(seen) == 2


def test_gives_up_after_configured_retries() -> None:
    wrapped, seen = _run_wrapper(1, [_validation_error(), _validation_error()])
    with pytest.raises(ValidationError):
        wrapped.invoke("Is this enough?")
    assert len(seen) == 2


def test_include_raw_is_not_wrapped() -> None:
    structured = _local_model().with_structured_output(_Verdict, include_raw=True)
    assert getattr(structured, "name", None) != "structured_output_with_validation_retry"
