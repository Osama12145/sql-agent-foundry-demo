from datetime import date, datetime
from typing import Any, Literal, Optional, TypedDict

# These types used to live in state.py alongside the LangGraph AgentState. The
# graph is gone, but they were always display types rather than graph types, so
# they live with the display policy now and this module stands on its own.
ChartType = Literal["table", "kpi", "bar", "line"]


class DisplayHint(TypedDict, total=False):
    chart_type: ChartType
    x: str
    y: str
    title: str
    reason: str


class DisplaySpec(TypedDict, total=False):
    chart_type: ChartType
    x: Optional[str]
    y: Optional[str]
    title: str
    reason: str


def decide_display(
    rows: list[dict[str, Any]],
    columns: list[str],
    hint: DisplayHint | None,
) -> DisplaySpec:
    if not rows or not columns:
        return _table("No rows to visualize.")

    numeric_columns = [column for column in columns if _column_is_numeric(rows, column)]
    date_columns = [column for column in columns if _column_is_date(rows, column)]
    text_columns = [
        column
        for column in columns
        if column not in numeric_columns and column not in date_columns
    ]

    # A true scalar is always clearer as a KPI, regardless of the model hint.
    if len(rows) == 1 and len(columns) == 1 and len(numeric_columns) == 1:
        return {
            "chart_type": "kpi",
            "x": None,
            "y": numeric_columns[0],
            "title": _title_from_hint(hint, numeric_columns[0]),
            "reason": "A single numeric result is clearer as a KPI.",
        }

    # The hint captures user intent, but actual rows are more reliable. Accept it
    # only when its chart type and axes match the returned result shape.
    if hint and _hint_matches_shape(hint, columns, numeric_columns, date_columns, text_columns):
        return {
            "chart_type": hint["chart_type"],
            "x": hint.get("x"),
            "y": hint.get("y"),
            "title": _title_from_hint(hint, "Query result"),
            "reason": hint.get("reason", "The LLM hint matches the result shape."),
        }

    if len(columns) == 2 and date_columns and numeric_columns:
        return {
            "chart_type": "line",
            "x": date_columns[0],
            "y": numeric_columns[0],
            "title": "Trend over time",
            "reason": "Date plus numeric data is best shown as a line chart.",
        }

    if len(columns) == 2 and text_columns and numeric_columns:
        return {
            "chart_type": "bar",
            "x": text_columns[0],
            "y": numeric_columns[0],
            "title": "Comparison",
            "reason": "Category plus numeric data is best shown as a bar chart.",
        }

    # A table is the lossless fallback when the result does not match a safe,
    # supported chart shape.
    return _table("The result shape is safest to display as a table.")


def _hint_matches_shape(
    hint: DisplayHint,
    columns: list[str],
    numeric_columns: list[str],
    date_columns: list[str],
    text_columns: list[str],
) -> bool:
    chart_type = hint.get("chart_type")
    x = hint.get("x")
    y = hint.get("y")

    if chart_type == "table":
        return True
    if chart_type == "kpi":
        return len(columns) == 1 and y in numeric_columns
    if chart_type == "line":
        return len(columns) == 2 and x in date_columns and y in numeric_columns
    if chart_type == "bar":
        return len(columns) == 2 and x in text_columns and y in numeric_columns
    return False


def _column_is_numeric(rows: list[dict[str, Any]], column: str) -> bool:
    values = [row.get(column) for row in rows if row.get(column) is not None]
    return bool(values) and all(isinstance(value, (int, float)) for value in values)


def _column_is_date(rows: list[dict[str, Any]], column: str) -> bool:
    values = [row.get(column) for row in rows if row.get(column) is not None]
    if not values:
        return False

    for value in values:
        if not isinstance(value, str):
            return False
        try:
            date.fromisoformat(value[:10])
            continue
        except ValueError:
            pass

        try:
            datetime.strptime(value, "%Y-%m")
        except ValueError:
            return False
    return True


def _title_from_hint(hint: DisplayHint | None, fallback: str) -> str:
    if hint and hint.get("title"):
        return hint["title"]
    return fallback.replace("_", " ").title()


def _table(reason: str) -> DisplaySpec:
    return {
        "chart_type": "table",
        "x": None,
        "y": None,
        "title": "Query Result",
        "reason": reason,
    }
