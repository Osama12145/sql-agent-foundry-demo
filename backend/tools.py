"""Tools the Foundry agent may call, and the code that executes them.

Foundry Agent Service runs the orchestration loop server-side: the model decides
which tool to call, in what order, and when to stop. The tool *bodies* still run
here, in our own process, which is the whole point of this design:

- The SQL safety boundary (backend/validators.py) stays ours.
- Database credentials never leave our backend.
- Display policy (backend/display.py) stays ours.

So Azure owns the workflow, and we own the guarantees.
"""

import json
from typing import Any

from backend.db import execute_select, load_schema
from backend.validators import validate_select_sql

SCHEMA_TOOL = "get_database_schema"
EXECUTE_TOOL = "execute_sql_query"
UNANSWERABLE_TOOL = "report_unanswerable"

# The graph version bounded repairs with MAX_REPAIR_ATTEMPTS. Foundry drives the
# loop now, so the bound has to live in the tool layer instead: once the budget
# is gone we stop executing and tell the model to give up. Without this, a model
# that keeps producing invalid SQL could bill an unbounded number of turns.
MAX_EXECUTE_ATTEMPTS = 3


# Flat shape on purpose: Foundry's FunctionToolParam is the Responses API format
# ({name, description, parameters, type}), not the nested Chat Completions format
# ({type, function: {...}}). These stay plain dicts so this module has no Azure
# import, which keeps the tool tests provider-agnostic.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": SCHEMA_TOOL,
        "description": (
            "Return the retail database schema: tables, columns, types, "
            "primary keys, and foreign keys. Call this first, before writing SQL."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": EXECUTE_TOOL,
        "description": (
            "Validate and run one read-only SELECT query against the retail "
            "database, and state how the result should be displayed. Returns the "
            "result rows, or an error message to correct and retry."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": (
                        "One SELECT statement, starting with SELECT. No semicolon, "
                        "no CTE, no write statements."
                    ),
                },
                "explanation": {
                    "type": "string",
                    "description": "One or two sentences on what the query computes.",
                },
                "chart_type": {
                    "type": "string",
                    "enum": ["kpi", "line", "bar", "table"],
                    "description": (
                        "kpi for a single number, line for a time series, bar for one "
                        "category plus one number, table for detailed rows."
                    ),
                },
                "x": {
                    "type": "string",
                    "description": "Column for the x axis. Omit for kpi and table.",
                },
                "y": {
                    "type": "string",
                    "description": "Column holding the numeric value. Omit for table.",
                },
                "title": {
                    "type": "string",
                    "description": "Short human-readable title for the result.",
                },
            },
            "required": ["sql", "explanation", "chart_type"],
        },
    },
    {
        "type": "function",
        "name": UNANSWERABLE_TOOL,
        "description": (
            "Call this instead of writing SQL when the question cannot be answered "
            "from the retail schema, for example world knowledge, personal identity, "
            "or data the schema does not contain. Never guess with a placeholder row."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief reason this database cannot answer the question.",
                },
            },
            "required": ["reason"],
        },
    },
]


class QueryOutcome:
    """Everything the agent produced during one run, for the API response.

    Foundry keeps the conversation; this keeps the structured facts our frontend
    needs. Tool calls write into it as they execute.
    """

    def __init__(self) -> None:
        self.sql: str | None = None
        self.sql_explanation: str | None = None
        self.rows: list[dict[str, Any]] = []
        self.columns: list[str] = []
        self.display_hint: dict[str, Any] | None = None
        self.out_of_scope: bool = False
        self.error: str | None = None
        self.execute_attempts: int = 0


def _run_schema_tool() -> dict[str, Any]:
    return {"schema": load_schema()}


def _run_execute_tool(arguments: dict[str, Any], outcome: QueryOutcome) -> dict[str, Any]:
    sql = (arguments.get("sql") or "").strip()
    if not sql:
        return {"ok": False, "error": "No sql argument was provided."}

    if outcome.execute_attempts >= MAX_EXECUTE_ATTEMPTS:
        return {
            "ok": False,
            "give_up": True,
            "error": (
                f"Attempt budget of {MAX_EXECUTE_ATTEMPTS} queries is exhausted. "
                "Stop calling tools and explain that the query could not be completed."
            ),
        }

    outcome.execute_attempts += 1
    outcome.sql_explanation = arguments.get("explanation")

    # Validate here as well as inside execute_select, for two different reasons:
    # this call gives us the *rewritten* SQL (ensure_limit adds the row cap) so the
    # dashboard shows what actually ran rather than what the model asked for, and
    # it lets us return a validation error without opening a connection.
    # execute_select still re-validates, because db.py must never trust a caller.
    is_valid, reason, safe_sql = validate_select_sql(sql)
    outcome.sql = safe_sql or sql

    hint = {
        "chart_type": arguments.get("chart_type") or "table",
        "x": arguments.get("x"),
        "y": arguments.get("y"),
        "title": arguments.get("title"),
    }
    outcome.display_hint = hint

    def _failure(message: str) -> dict[str, Any]:
        outcome.error = message
        outcome.rows = []
        outcome.columns = []
        remaining = MAX_EXECUTE_ATTEMPTS - outcome.execute_attempts
        return {
            "ok": False,
            "error": message,
            "attempts_remaining": remaining,
            "hint": (
                "Fix the query and call the tool again."
                if remaining > 0
                else "No attempts remain. Stop and explain the failure."
            ),
        }

    if not is_valid or safe_sql is None:
        return _failure(reason or "Invalid SQL query.")

    try:
        # execute_select validates again before it touches the database, so unsafe
        # SQL never reaches a connection no matter what the model asked for.
        rows, columns = execute_select(safe_sql)
    except Exception as exc:
        return _failure(str(exc))

    # Hold the model to the contract it just declared. display.py requires one
    # column for a kpi and exactly two for a line or bar chart, and that rule is
    # a real safeguard: a three-column result plotted as two axes silently drops
    # a column. Rather than weaken it, reject the mismatch here so the agent
    # corrects its own query. Without this the same question renders as a bar
    # chart or a table depending on whether the model happened to select a
    # redundant id, which is worse than either outcome on its own.
    required = {"kpi": 1, "line": 2, "bar": 2}.get(hint["chart_type"])
    if required is not None and len(columns) != required:
        return _failure(
            f"chart_type '{hint['chart_type']}' needs exactly {required} "
            f"column(s), but the query returned {len(columns)}: {columns}. "
            "Re-run with only the columns the chart needs, or choose "
            "chart_type 'table' instead."
        )

    outcome.error = None
    outcome.rows = rows
    outcome.columns = columns
    return {
        "ok": True,
        "columns": columns,
        "row_count": len(rows),
        # Truncated on purpose: the model needs enough rows to summarize, not the
        # whole payload. The frontend gets the full set from QueryOutcome.
        "rows": rows[:20],
    }


def _run_unanswerable_tool(
    arguments: dict[str, Any], outcome: QueryOutcome
) -> dict[str, Any]:
    outcome.out_of_scope = True
    outcome.sql = None
    outcome.rows = []
    outcome.columns = []
    outcome.display_hint = None
    return {
        "ok": True,
        "acknowledged": True,
        "reason": arguments.get("reason"),
        "next": "Explain to the user, in one or two sentences, why this database cannot answer.",
    }


def dispatch_tool_call(
    name: str,
    raw_arguments: str,
    outcome: QueryOutcome,
) -> str:
    """Execute one tool call and return the JSON string to hand back to Foundry."""
    try:
        arguments = json.loads(raw_arguments) if raw_arguments else {}
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "error": "Tool arguments were not valid JSON."})

    if name == SCHEMA_TOOL:
        result = _run_schema_tool()
    elif name == EXECUTE_TOOL:
        result = _run_execute_tool(arguments, outcome)
    elif name == UNANSWERABLE_TOOL:
        result = _run_unanswerable_tool(arguments, outcome)
    else:
        result = {"ok": False, "error": f"Unknown tool: {name}"}

    return json.dumps(result, default=str)
