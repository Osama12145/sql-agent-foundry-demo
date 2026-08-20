# Architecture

Three views: the agent workflow, the system as a whole, then one run in detail.

## 1. Agent workflow

The same kind of node-and-edge view a graph framework would produce, with one
decisive difference: **the branching node is not ours.**

In a LangGraph build, every diamond in this diagram would be a conditional edge
we wrote, and the arrows would be routing rules in our code. Here there is a
single decision point, `Foundry decides next action`, and it runs in Azure. We do
not choose whether the agent fetches the schema first, whether it retries after a
rejected query, or when it stops — we only declare the tools it may call and
enforce what those tools are allowed to do.

Colour tells you who owns each box: **blue** is Azure, **orange** is a tool the
agent may call, **red** is the safety boundary that no model output can bypass,
**grey** is ordinary application code.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
    __start__([START]):::first
    publish_agent(publish_agent):::ours
    start_run(start_run):::ours
    decide{{"Foundry decides<br/>next action"}}:::azure
    get_database_schema(get_database_schema):::tool
    execute_sql_query(execute_sql_query):::tool
    report_unanswerable(report_unanswerable):::tool
    validate_sql(validate_sql):::guard
    run_query(run_query):::guard
    return_tool_error(return_tool_error):::ours
    decide_display(decide_display):::ours
    final_answer(final_answer):::azure
    fail(fail):::ours
    __end__([END]):::last

    __start__ --> publish_agent;
    publish_agent -->|instructions + tool schemas| start_run;
    start_run --> decide;

    decide -. needs_schema .-> get_database_schema;
    decide -. has_sql .-> execute_sql_query;
    decide -. outside_schema .-> report_unanswerable;
    decide -. no_tool_call .-> final_answer;
    decide -. iteration_limit_reached .-> fail;

    get_database_schema -->|schema| decide;

    execute_sql_query --> validate_sql;
    validate_sql -. valid_SQL .-> run_query;
    validate_sql -. blocked_SQL .-> return_tool_error;
    run_query -. success .-> decide;
    run_query -. execution_error .-> return_tool_error;
    return_tool_error -. attempts_remaining .-> decide;
    return_tool_error -. attempt_budget_spent .-> fail;

    report_unanswerable --> decide;

    final_answer -. rows_returned .-> decide_display;
    final_answer -. out_of_scope .-> __end__;
    decide_display --> __end__;
    fail --> __end__;

    classDef default fill:#eef4f8,stroke:#486270,color:#14252e,line-height:1.2
    classDef first fill:#ffffff,stroke:#2f6f64,color:#14252e
    classDef last fill:#d9eee8,stroke:#2f6f64,color:#14252e
    classDef azure fill:#e8f0fe,stroke:#1a73e8,color:#14252e
    classDef tool fill:#fef7e0,stroke:#b06000,color:#14252e
    classDef guard fill:#fce8e6,stroke:#d93025,color:#14252e
    classDef ours fill:#eef4f8,stroke:#486270,color:#14252e
```

Read the loop this way: every tool result returns to `Foundry decides`, and the
agent keeps taking turns until it makes no further tool call. Two counters keep
that loop finite, and both are ours rather than the orchestrator's —
`MAX_EXECUTE_ATTEMPTS` caps rejected queries, `MAX_TOOL_ITERATIONS` caps the
conversation. Nothing in Azure would stop the loop on its own.

Note also what `validate_sql` and `run_query` are doing in the middle of the
diagram. Even though the orchestration is remote, every path from the model to
the database still passes through them, and `report_unanswerable` gives the agent
a way to decline that produces no SQL at all.

## 2. System architecture

Blue dashed is Azure. Red is our safety boundary. Everything else is our code.

```mermaid
flowchart TB
    USER([User])

    subgraph CLIENT["Presentation"]
        UI["Streamlit<br/>frontend/app.py<br/>:8501"]
    end

    subgraph SERVICE["Application service"]
        API["FastAPI<br/>backend/main.py<br/>POST /query, GET /health"]
        AGENTMOD["backend/agent.py<br/>publishes the agent definition,<br/>runs the tool-call loop"]
        TOOLS["backend/tools.py<br/>tool schemas + implementations<br/>MAX_EXECUTE_ATTEMPTS"]
        DISP["backend/display.py<br/>chooses KPI / line / bar / table<br/>from the real result shape"]
    end

    subgraph AZURE["Azure AI Foundry"]
        PROJ["Foundry project<br/>account kind: AIServices"]
        AGENT["Agent: retail-sql-agent<br/>instructions + tool schemas"]
        MODEL["Model deployment<br/>gpt-5-mini 2025-08-07"]
        PROJ --- AGENT
        AGENT --- MODEL
    end

    subgraph DATA["Data"]
        VAL["backend/validators.py<br/>SELECT only, single statement,<br/>blocked keywords, row cap"]
        DB["backend/db.py<br/>read-only connection"]
        STORE[("retail.db<br/>customers, products,<br/>orders, order_items")]
        VAL --> DB
        DB --> STORE
    end

    ENTRA["Entra ID<br/>DefaultAzureCredential<br/>no API key"]

    USER --> UI
    UI -->|"question (JSON)"| API
    API --> AGENTMOD
    AGENTMOD <-->|"responses API<br/>turn / tool result"| AGENT
    AGENTMOD --> TOOLS
    TOOLS --> VAL
    TOOLS -->|schema| DB
    API --> DISP
    DISP -->|display spec| UI
    ENTRA -.->|bearer token| AGENTMOD

    classDef azure fill:#e8f0fe,stroke:#1a73e8,stroke-width:2px,stroke-dasharray: 6 3
    classDef safety fill:#fce8e6,stroke:#d93025,stroke-width:2px
    classDef store fill:#e6f4ea,stroke:#137333
    class AZURE azure
    class VAL,DB safety
    class STORE store
```

**The division that matters.** Azure decides *what to do next*. We decide *what is
allowed to happen*. Tool bodies execute in our process, so database credentials
never reach Azure and no model output touches the database without passing
`validate_select_sql` first.

## 3. One agent run

Measured on this build: about 2 tool calls and 3 model turns per question, 8–21
seconds end to end, of which our own code accounts for roughly 0.002 seconds.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant API as FastAPI<br/>backend/main.py
    participant Run as agent.py<br/>tool-call loop
    participant Foundry as Foundry Agent Service<br/>(Azure)
    participant Tools as tools.py
    participant Guard as validators.py
    participant DB as db.py (read-only)
    participant Disp as display.py

    User->>API: POST /query {question}
    API->>Run: answer_question(question)
    Run->>Foundry: publish agent version<br/>(instructions + tool schemas)
    Run->>Foundry: responses.create(question)

    rect rgb(232, 240, 254)
        note over Foundry: turn 1 — Foundry decides it needs the schema
        Foundry-->>Run: function_call: get_database_schema
        Run->>Tools: dispatch_tool_call
        Tools->>DB: load_schema()
        DB-->>Tools: tables, columns, keys
        Tools-->>Run: schema JSON
        Run->>Foundry: function_call_output
    end

    rect rgb(232, 240, 254)
        note over Foundry: turn 2 — writes SQL and a display hint
        Foundry-->>Run: function_call: execute_sql_query<br/>{sql, chart_type, x, y, title}
        Run->>Tools: dispatch_tool_call
        Tools->>Guard: validate_select_sql(sql)

        alt SQL rejected
            Guard-->>Tools: reason
            Tools-->>Run: {ok:false, error, attempts_remaining}
            Run->>Foundry: function_call_output
            note over Foundry: corrects and retries,<br/>bounded by MAX_EXECUTE_ATTEMPTS = 3
        else SQL accepted
            Guard-->>Tools: safe_sql (row cap applied)
            Tools->>DB: execute_select(safe_sql)
            DB-->>Tools: rows, columns
            Tools-->>Run: {ok:true, rows[:20], row_count}
            Run->>Foundry: function_call_output
        end
    end

    rect rgb(232, 240, 254)
        note over Foundry: turn 3 — no more tool calls, writes the answer
        Foundry-->>Run: output_text (final answer)
    end

    Run->>Disp: decide_display(rows, columns, hint)
    note right of Disp: the agent's chart_type is only a hint;<br/>it is overridden when it does not fit<br/>the real column shape
    Disp-->>Run: display spec
    Run-->>API: answer, sql, rows, columns, display
    API-->>User: JSON response

    note over Run,Foundry: whole conversation bounded by<br/>MAX_TOOL_ITERATIONS = 8
```

## 4. Where the time goes

Latency is dominated by model turns, not by data. Measured over 3 repeats of the
four demo questions:

| Component | Time |
| --- | --- |
| `load_schema()` + `execute_select()` — all our database work | **0.002s** |
| One bare model round trip | ~9.5s |
| Model turns per question | 3 |
| Full question, end to end | median 14.9s, range 11.0–22.7s |

Dataset size is not a factor: 6 products, 10 orders, 17 order items. Making the
database ten times bigger would not move these numbers measurably; removing a
model turn would.

The three turns are structural, not waste:

1. the agent asks for the schema,
2. it sends SQL and gets rows back,
3. it writes the answer.

Turn 1 is the one that could be removed, by baking the schema into the agent
instructions at publish time instead of exposing it as a tool. That trades a
round trip for a schema that only refreshes when the agent is republished.

### A warning about measuring this

Run-to-run variance is wide — the same question ranged from 11.0s to 22.7s at a
fixed configuration. A single sample proves nothing here. An earlier comparison
in this project appeared to show a 37% gain from lowering reasoning effort; it
did not survive repetition, and the measurement harness itself was flawed:
`ensure_agent()` is cached, so calling `answer_question()` after publishing a
definition re-publishes from `agent_definition()` and silently overrides the
version under test. Restart the process between configurations.
