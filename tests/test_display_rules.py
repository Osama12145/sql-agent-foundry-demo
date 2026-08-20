from backend.display import decide_display


def test_single_number_becomes_kpi():
    display = decide_display(
        rows=[{"total_revenue": 1000.0}],
        columns=["total_revenue"],
        hint={"chart_type": "bar", "x": "category", "y": "total_revenue"},
    )

    assert display["chart_type"] == "kpi"
    assert display["y"] == "total_revenue"


def test_date_and_number_becomes_line_chart():
    display = decide_display(
        rows=[
            {"month": "2026-01-01", "revenue": 100.0},
            {"month": "2026-02-01", "revenue": 200.0},
        ],
        columns=["month", "revenue"],
        hint=None,
    )

    assert display["chart_type"] == "line"
    assert display["x"] == "month"
    assert display["y"] == "revenue"


def test_matching_bar_hint_is_respected():
    display = decide_display(
        rows=[
            {"category": "Electronics", "revenue": 5000.0},
            {"category": "Furniture", "revenue": 3000.0},
        ],
        columns=["category", "revenue"],
        hint={
            "chart_type": "bar",
            "x": "category",
            "y": "revenue",
            "title": "Revenue by Category",
        },
    )

    assert display["chart_type"] == "bar"
    assert display["title"] == "Revenue by Category"


def test_month_and_number_becomes_line_chart():
    display = decide_display(
        rows=[
            {"month": "2026-01", "revenue": 100.0},
            {"month": "2026-02", "revenue": 200.0},
        ],
        columns=["month", "revenue"],
        hint=None,
    )

    assert display["chart_type"] == "line"
    assert display["x"] == "month"


def test_category_is_not_hidden_inside_kpi():
    display = decide_display(
        rows=[{"city": "Riyadh", "customer_count": 2}],
        columns=["city", "customer_count"],
        hint={"chart_type": "bar", "x": "city", "y": "customer_count"},
    )

    assert display["chart_type"] == "bar"
    assert display["x"] == "city"


def test_detailed_result_becomes_table():
    display = decide_display(
        rows=[
            {"name": "Laptop", "category": "Electronics", "price": 5200.0},
            {"name": "Desk", "category": "Furniture", "price": 1800.0},
        ],
        columns=["name", "category", "price"],
        hint={"chart_type": "bar", "x": "category", "y": "price"},
    )

    assert display["chart_type"] == "table"
