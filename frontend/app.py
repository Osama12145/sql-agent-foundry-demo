import os

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv


load_dotenv()
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000/query")


st.set_page_config(page_title="Agentic SQL Dashboard", layout="wide")
st.title("Agentic SQL Dashboard")

sample_questions = [
    "What is the total revenue for completed orders?",
    "Show monthly revenue for completed orders.",
    "What are the top 5 products by revenue?",
    "List each product with its category and price.",
]

if "question" not in st.session_state:
    st.session_state.question = sample_questions[0]

selected = st.selectbox("Sample questions", sample_questions)
if st.button("Use sample"):
    st.session_state.question = selected

question = st.text_input(
    "Ask a question about the retail database",
    key="question",
)

if st.button("Run query", type="primary"):
    if not question.strip():
        st.warning("Please enter a question.")
        st.stop()

    with st.spinner("Running Foundry agent..."):
        try:
            response = requests.post(
                API_URL,
                json={"question": question},
                # The agent takes several model turns (schema, query, answer), each
                # spending reasoning tokens, so this waits well past a single call.
                timeout=180,
            )
        except requests.RequestException:
            st.error("Could not reach the API. Make sure the backend is running.")
            st.stop()

    if response.status_code != 200:
        try:
            message = response.json().get("detail", response.text)
        except ValueError:
            message = response.text
        st.error(message)
        st.stop()

    result = response.json()
    if result.get("error"):
        st.warning(result["error"])

    st.subheader("Answer")
    st.write(result.get("answer") or "No answer returned.")

    if result.get("out_of_scope"):
        st.info("No SQL was executed because this question is outside the available retail database.")
        st.stop()

    with st.expander("Generated SQL"):
        st.code(result.get("sql") or "", language="sql")
        if result.get("sql_explanation"):
            st.write(result["sql_explanation"])

    rows = result.get("rows") or []
    display = result.get("display") or {"chart_type": "table"}

    if not rows:
        st.info("No rows returned.")
        st.stop()

    df = pd.DataFrame(rows)
    chart_type = display.get("chart_type", "table")
    x_column = display.get("x")
    y_column = display.get("y")

    st.subheader(display.get("title", "Result"))

    # The frontend only renders the backend decision; chart policy stays in the agent.
    if chart_type == "kpi" and y_column:
        st.metric(y_column.replace("_", " ").title(), df.iloc[0][y_column])
    elif chart_type == "line" and x_column and y_column:
        st.line_chart(
            df,
            x=x_column,
            y=y_column,
            # Boolean container sizing works across older Streamlit versions too.
            use_container_width=True,
            height=360,
        )
    elif chart_type == "bar" and x_column and y_column:
        st.bar_chart(
            df,
            x=x_column,
            y=y_column,
            use_container_width=True,
            height=360,
        )
    else:
        st.dataframe(df, use_container_width=True)

    if chart_type != "table":
        with st.expander("Raw rows"):
            st.dataframe(df, use_container_width=True)
