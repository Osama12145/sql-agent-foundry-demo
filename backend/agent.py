"""Foundry Agent Service integration: agent definition, and one run per question.

This module replaces graph.py, state.py, and llm.py from the LangGraph version.

What moved to Azure:
    the orchestration loop. Foundry decides which tool to call, reads the tool
    result, retries on error, and decides when it has enough to answer.

What stayed here:
    the tool bodies (backend/tools.py), the SQL safety boundary
    (backend/validators.py), database access (backend/db.py), and display policy
    (backend/display.py). Azure owns the workflow; we still own the guarantees.

Authentication is Entra ID, not an API key. Foundry Agent Service does not accept
account keys for the agents data plane, so there is no secret in .env at all —
locally this uses your `az login` session, and on App Service it would use a
managed identity with no code change.
"""

import os
from functools import lru_cache
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    FunctionToolParam,
    PromptAgentDefinition,
    Reasoning,
)
from azure.identity import DefaultAzureCredential

from backend.display import decide_display
from backend.prompts import AGENT_INSTRUCTIONS, AGENT_NAME
from backend.tools import TOOL_DEFINITIONS, QueryOutcome, dispatch_tool_call

# Each turn is one model call, so this bounds cost per question. The tool layer
# separately bounds SQL attempts; this bounds the whole conversation.
MAX_TOOL_ITERATIONS = 8


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        # RuntimeError on purpose: main.py maps it to a 503, which is the right
        # signal for "the service is misconfigured", not "your question failed".
        raise RuntimeError(f"{name} is missing.")
    return value


@lru_cache(maxsize=1)
def _project_client() -> AIProjectClient:
    # allow_preview is required to point an OpenAI client at an agent endpoint.
    return AIProjectClient(
        endpoint=_require_env("PROJECT_ENDPOINT"),
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )


def _tool_params() -> list[FunctionToolParam]:
    return [
        FunctionToolParam(
            name=tool["name"],
            description=tool["description"],
            parameters=tool["parameters"],
        )
        for tool in TOOL_DEFINITIONS
    ]


def _reasoning_override() -> dict[str, Any]:
    """Send a reasoning effort only when one is explicitly configured."""
    effort = os.getenv("AGENT_REASONING_EFFORT", "").strip()
    return {"reasoning": Reasoning(effort=effort)} if effort else {}


def agent_definition() -> PromptAgentDefinition:
    """The agent as code: model, instructions, and the tools it may call."""
    return PromptAgentDefinition(
        model=_require_env("MODEL_DEPLOYMENT_NAME"),
        instructions=AGENT_INSTRUCTIONS,
        tools=_tool_params(),
        # Left at the service default, which answered the demo set correctly 6/6
        # on the bar question that is most sensitive to shallow reasoning.
        # "minimal" was measurably worse at following the display rules.
        # Set AGENT_REASONING_EFFORT to experiment. If you do, note that
        # ensure_agent() is cached: publishing a definition and then calling
        # answer_question() in the same process re-publishes from this function
        # and silently overrides what you just published. Restart the process
        # between settings, or the comparison measures the same version twice.
        **_reasoning_override(),
    )


@lru_cache(maxsize=1)
def ensure_agent() -> str:
    """Publish the current definition as a new agent version, and return its name.

    Publishing on startup keeps the deployed agent in lockstep with this
    repository: the instructions and tool schemas that are live are always the
    ones in the code, never a stale copy edited in the portal. Foundry keeps the
    version history, so this is additive rather than destructive.
    """
    client = _project_client()
    client.agents.create_version(AGENT_NAME, definition=agent_definition())
    return AGENT_NAME


def _function_calls(response: Any) -> list[Any]:
    return [
        item
        for item in (getattr(response, "output", None) or [])
        if getattr(item, "type", None) == "function_call"
    ]


def answer_question(question: str) -> dict[str, Any]:
    """Run one question through the Foundry agent and return the API payload."""
    outcome = QueryOutcome()
    client = _project_client()
    openai_client = client.get_openai_client(agent_name=ensure_agent())

    response = openai_client.responses.create(
        input=[{"role": "user", "content": question}],
    )

    exhausted = True
    for _ in range(MAX_TOOL_ITERATIONS):
        calls = _function_calls(response)
        if not calls:
            exhausted = False
            break

        tool_outputs = [
            {
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": dispatch_tool_call(call.name, call.arguments, outcome),
            }
            for call in calls
        ]
        response = openai_client.responses.create(
            previous_response_id=response.id,
            input=tool_outputs,
        )

    if exhausted:
        outcome.error = (
            f"The agent did not finish within {MAX_TOOL_ITERATIONS} tool turns."
        )

    answer = (getattr(response, "output_text", None) or "").strip()

    if outcome.out_of_scope:
        return {
            "answer": answer or "This retail database cannot answer that question.",
            "sql": None,
            "sql_explanation": None,
            "rows": [],
            "columns": [],
            "display": None,
            "error": None,
            "out_of_scope": True,
        }

    # Display policy stays ours: the agent's chart_type is only a hint, and
    # decide_display overrides it when the real result shape disagrees.
    display = (
        decide_display(
            rows=outcome.rows,
            columns=outcome.columns,
            hint=outcome.display_hint,
        )
        if outcome.rows
        else None
    )

    return {
        "answer": answer or "No answer was produced.",
        "sql": outcome.sql,
        "sql_explanation": outcome.sql_explanation,
        "rows": outcome.rows,
        "columns": outcome.columns,
        "display": display,
        "error": outcome.error,
        "out_of_scope": False,
    }
