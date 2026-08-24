"""
LLM token usage tracking, via a LangChain callback -- not by changing any
node's `with_structured_output(...)` call to `include_raw=True` (which
would touch every node across Phases 5-8 just to get at usage metadata
buried in the raw response). A callback observes every underlying chat
model call transparently, regardless of how deep in the graph it happens,
so no Phase 1-8 node code changes at all.

"Where supported by the OpenAI response metadata, capture... Do not
estimate usage if actual usage metadata is available" (Phase 9 spec) --
this reads whatever `response.llm_output["token_usage"]` the provider
actually returned; it never estimates or guesses.
"""

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


class TokenUsageTracker(BaseCallbackHandler):
    """Accumulates input/output/total tokens across every LLM call made
    while this tracker is attached (see workers/analysis_worker.py,
    passed via `config={"callbacks": [tracker]}` to one graph run)."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        usage = (response.llm_output or {}).get("token_usage") or {}
        self.input_tokens += usage.get("prompt_tokens", 0)
        self.output_tokens += usage.get("completion_tokens", 0)
        self.total_tokens += usage.get("total_tokens", 0)

    @property
    def usage(self) -> dict[str, int] | None:
        """None (not zeros) if no call ever reported usage metadata at
        all -- distinguishes "genuinely zero tokens" from "the provider
        never told us," per "do not estimate usage.\""""

        if self.input_tokens == 0 and self.output_tokens == 0 and self.total_tokens == 0:
            return None
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }
