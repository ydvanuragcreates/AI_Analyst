"""
Manual, low-level tool-calling demonstration.

Run with a real OPENAI_API_KEY set (this makes real OpenAI calls):

    cd backend
    python -m tests.manual_tool_call

This intentionally does NOT use LangGraph, ToolNode, or create_agent. It
hand-rolls the loop that those abstractions normally do for you, so the
underlying mechanism is fully visible:

    User message
        -> model_with_tools.invoke(messages)   # ask the model what to do
        -> AIMessage                            # the model's response
        -> ai_msg.tool_calls                    # what the model wants to run
        -> (our code) tool.invoke(tool_call)     # WE execute it, not the model
        -> ToolMessage                          # the tool's result
        -> messages.append(...); repeat          # send the result back
        -> final AIMessage (no tool_calls)       # the model's answer

At no point does the model execute Python or SQL. It only ever *requests*
a tool by name with arguments; this script is the thing that actually
calls the tool and hands the result back.

A small in-memory dataset is registered directly against DuckDBService so
this script is self-contained -- it doesn't require the FastAPI server to
be running or a dataset to already be uploaded.
"""

import uuid

import pandas as pd
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agent.nodes import AGENT_TOOLS, get_model_with_tools
from app.agent.prompts import ANALYST_SYSTEM_PROMPT
from app.services.duckdb_service import get_duckdb_service

MAX_TURNS = 5
SECTION = "=" * 40


def _print_section(title: str, body: str = "") -> None:
    print(f"\n{SECTION}\n{title}\n{SECTION}\n")
    if body:
        print(body)


def _setup_demo_dataset() -> str:
    """Register a small sample dataset directly in DuckDB and return its dataset_id."""

    service = get_duckdb_service()
    dataset_id = str(uuid.uuid4())
    dataframe = pd.DataFrame(
        {
            "region": ["Delhi", "Mumbai", "Bangalore", "Delhi", "Chennai", "Mumbai"],
            "revenue": [125000, 110000, 98000, 87000, 76000, 54000],
        }
    )
    service.register_dataset(dataset_id, dataframe)
    return dataset_id


def run_demo(question: str) -> None:
    dataset_id = _setup_demo_dataset()
    tools_by_name = {t.name: t for t in AGENT_TOOLS}
    model_with_tools = get_model_with_tools()

    _print_section("USER", question)

    messages: list = [SystemMessage(content=ANALYST_SYSTEM_PROMPT), HumanMessage(content=question)]

    try:
        for _ in range(MAX_TURNS):
            # STEP 1: ask the model what it wants to do next.
            ai_msg: AIMessage = model_with_tools.invoke(messages)
            messages.append(ai_msg)

            if not ai_msg.tool_calls:
                # No tool_calls on this AIMessage -> the model is done.
                _print_section("FINAL RESPONSE", str(ai_msg.content))
                return

            for tool_call in ai_msg.tool_calls:
                _print_section(
                    "MODEL TOOL CALL",
                    f"Tool: {tool_call['name']}\n"
                    f"Arguments: {tool_call['args']}\n"
                    f"Tool call ID: {tool_call['id']}",
                )

                # STEP 2: WE execute the tool -- the model never does.
                # dataset_id is intentionally not something the model can
                # see or set (it's InjectedState, hidden from the tool's
                # schema -- see agent/tools/); our own code supplies it
                # here from context THIS SCRIPT controls, exactly as
                # ToolNode does automatically inside the real graph.
                tool = tools_by_name[tool_call["name"]]
                call_with_dataset_id = {**tool_call, "args": {**tool_call["args"], "dataset_id": dataset_id}}
                tool_message: ToolMessage = tool.invoke(call_with_dataset_id)

                _print_section("TOOL RESULT", tool_message.content)

                # STEP 3: append the tool result to the conversation.
                messages.append(tool_message)

            # STEP 4 (implicit): loop back to STEP 1 and send everything,
            # including the new ToolMessage(s), back to the model.

        _print_section("FINAL RESPONSE", "(gave up after MAX_TURNS without a final answer)")
    finally:
        get_duckdb_service().unregister_dataset(dataset_id)


if __name__ == "__main__":
    run_demo("Which region has the highest revenue?")
