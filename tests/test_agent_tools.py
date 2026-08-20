"""Tool-layer tests.

The graph-routing tests are gone with the graph: Foundry owns the loop now, and
its routing is not ours to unit test. What is still ours, and still worth
testing, is what the tools do with whatever the model sends them.

No test calls a real model or a real Azure endpoint.
"""

import json

import pytest

from backend import tools as tools_module
from backend.tools import (
    EXECUTE_TOOL,
    MAX_EXECUTE_ATTEMPTS,
    SCHEMA_TOOL,
    UNANSWERABLE_TOOL,
    QueryOutcome,
    dispatch_tool_call,
)


def _mock_execute(monkeypatch, rows, columns):
    monkeypatch.setattr(
        tools_module, "execute_select", lambda sql: (rows, columns)
    )


def test_tool_definitions_use_the_flat_responses_shape():
    # Foundry's FunctionToolParam is the Responses API format. A nested
    # {"function": {...}} block here would be silently dropped.
    for tool in tools_module.TOOL_DEFINITIONS:
        assert tool["type"] == "function"
        assert "name" in tool
        assert "function" not in tool
        assert tool["parameters"]["type"] == "object"


def test_schema_tool_returns_schema(monkeypatch):
    monkeypatch.setattr(tools_module, "load_schema", lambda: "Table: products")
    outcome = QueryOutcome()

    result = json.loads(dispatch_tool_call(SCHEMA_TOOL, "{}", outcome))

    assert result["schema"] == "Table: products"


def test_execute_tool_records_sql_rows_and_hint(monkeypatch):
    _mock_execute(monkeypatch, [{"total": 42}], ["total"])
    outcome = QueryOutcome()
    arguments = json.dumps(
        {
            "sql": "SELECT 42 AS total FROM products",
            "explanation": "Counts things.",
            "chart_type": "kpi",
            "y": "total",
            "title": "Total",
        }
    )

    result = json.loads(dispatch_tool_call(EXECUTE_TOOL, arguments, outcome))

    assert result["ok"] is True
    assert result["row_count"] == 1
    assert outcome.rows == [{"total": 42}]
    assert outcome.columns == ["total"]
    # The recorded SQL is the query that actually ran, row cap included, not the
    # raw string the model sent. The dashboard shows this, so it must match reality.
    assert outcome.sql == "SELECT 42 AS total FROM products LIMIT 100"
    assert outcome.display_hint["chart_type"] == "kpi"
    assert outcome.error is None


def test_execute_tool_reports_validation_failure_for_retry():
    # No execute_select mock needed: validation rejects this before any database
    # access, which is the property we care about.
    outcome = QueryOutcome()
    arguments = json.dumps(
        {
            "sql": "DELETE FROM products",
            "explanation": "Should never run.",
            "chart_type": "table",
        }
    )

    result = json.loads(dispatch_tool_call(EXECUTE_TOOL, arguments, outcome))

    assert result["ok"] is False
    assert "Only SELECT" in result["error"]
    assert result["attempts_remaining"] == MAX_EXECUTE_ATTEMPTS - 1
    assert outcome.rows == []


def test_execute_tool_stops_after_the_attempt_budget():
    outcome = QueryOutcome()
    arguments = json.dumps(
        {"sql": "DELETE FROM products", "explanation": "x", "chart_type": "table"}
    )

    for _ in range(MAX_EXECUTE_ATTEMPTS):
        dispatch_tool_call(EXECUTE_TOOL, arguments, outcome)

    # One more call past the budget must refuse rather than keep executing.
    result = json.loads(dispatch_tool_call(EXECUTE_TOOL, arguments, outcome))

    assert result["ok"] is False
    assert result["give_up"] is True
    assert outcome.execute_attempts == MAX_EXECUTE_ATTEMPTS


def test_unanswerable_tool_marks_out_of_scope():
    outcome = QueryOutcome()
    arguments = json.dumps({"reason": "The database holds retail data, not city data."})

    result = json.loads(dispatch_tool_call(UNANSWERABLE_TOOL, arguments, outcome))

    assert result["ok"] is True
    assert outcome.out_of_scope is True
    assert outcome.sql is None


def test_unknown_tool_is_reported_not_raised():
    outcome = QueryOutcome()

    result = json.loads(dispatch_tool_call("drop_everything", "{}", outcome))

    assert result["ok"] is False
    assert "Unknown tool" in result["error"]


def test_malformed_arguments_are_reported_not_raised():
    outcome = QueryOutcome()

    result = json.loads(dispatch_tool_call(EXECUTE_TOOL, "{not json", outcome))

    assert result["ok"] is False
    assert "valid JSON" in result["error"]


def test_execute_tool_truncates_rows_sent_to_the_model(monkeypatch):
    rows = [{"n": i} for i in range(50)]
    _mock_execute(monkeypatch, rows, ["n"])
    outcome = QueryOutcome()
    arguments = json.dumps(
        {"sql": "SELECT n FROM products", "explanation": "x", "chart_type": "table"}
    )

    result = json.loads(dispatch_tool_call(EXECUTE_TOOL, arguments, outcome))

    assert result["row_count"] == 50
    assert len(result["rows"]) == 20
    # The frontend still receives every row.
    assert len(outcome.rows) == 50
