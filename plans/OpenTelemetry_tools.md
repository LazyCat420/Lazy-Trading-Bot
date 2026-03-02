Understood. If you are handling sensitive trading algorithms and capital, keeping everything 100% local (air-gapped from third-party cloud trackers) is the safest approach.

To get enterprise-grade observability without data ever leaving your machine, your dev team needs to build a **Dual-Layer Local Logging System**:

1. **The Thought Ledger (DuckDB):** For your custom UI so you can see exactly what the bot was thinking per ticker.
2. **Arize Phoenix (Local Server):** A local OpenTelemetry dashboard for developers to trace the exact milliseconds and execution steps of the Python code.

Here is the exact plan to hand to your dev team.

***

### Epic: Local-Only LLM Observability & Auditing

#### Phase 1: The "Thought Ledger" (DuckDB Custom Logging)

**Goal:** Persist every single LLM prompt, injected context, and raw output into your local DuckDB so you can view it directly in your app's frontend.

**Ticket 1: Create the `llm_audit_logs` Schema**
In `app/database.py`, add a new table migration:

```sql
CREATE TABLE IF NOT EXISTS llm_audit_logs (
    id UUID PRIMARY KEY,
    cycle_id VARCHAR,          -- Groups logs by the current trading run
    ticker VARCHAR,            -- E.g., 'NVDA'
    agent_step VARCHAR,        -- E.g., 'News Summary', 'Final Decision'
    system_prompt TEXT,        -- The exact system prompt used
    user_context TEXT,         -- The raw data (news, prices) fed to the LLM
    raw_response TEXT,         -- What the LLM actually output
    parsed_json JSON,          -- The JSON extracted from the output (if valid)
    tokens_used INTEGER,
    execution_time_ms INTEGER,
    created_at TIMESTAMP
);
```

**Ticket 2: Intercept Traffic in `LLMService`**
In `app/services/llm_service.py` (which currently routes all Ollama traffic), update the `chat()` or `send_ollama_request()` methods to asynchronously insert a row into `llm_audit_logs` the moment Ollama returns a response.

* *Requirement:* Ensure this DB write is non-blocking so it doesn't slow down the pipeline.
* *Requirement:* If the LLM output fails JSON validation, the `parsed_json` column should be null, but the `raw_response` must contain the broken text so you can debug *why* it broke.

**Ticket 3: Add "Audit View" to the Frontend UI**
In your `terminal_app.js` / frontend:

* Add an "Audit" button next to each ticker in the Scoreboard.
* When clicked, it queries the backend `GET /api/audit/{ticker}` and displays a side-by-side view: **Left side** shows the raw data the LLM was given (prices/news), **Right side** shows the LLM's exact response.

#### Phase 2: Deep Local Tracing (Arize Phoenix)

**Goal:** When the application breaks or loops infinitely, your devs need a timeline view of execution (Search Database -> Fetch News -> Format Prompt -> Ollama Call). **Arize Phoenix** is open-source and runs entirely locally. No cloud connection is required.

**Ticket 4: Install & Boot Local Phoenix Server**

* Add dependencies: `pip install arize-phoenix openinference-instrumentation arize-otel`
* In your app's startup script or `main.py`, spawn the Phoenix server in the background:

    ```python
    import phoenix as px
    # Boots a local web UI on http://localhost:6006 - NO cloud connection
    session = px.launch_app() 
    ```

**Ticket 5: Instrument the Pipeline (OpenTelemetry)**
Instruct the devs to use OpenTelemetry to trace the functions. This automatically tracks how long things take and what variables were passed.

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from openinference.instrumentation import auto_instrument

# Route traces ONLY to the local Phoenix server
tracer_provider = TracerProvider()
tracer_provider.add_span_processor(
    SimpleSpanProcessor(OTLPSpanExporter("http://localhost:6006/v1/traces"))
)
auto_instrument()
```

* *Developer Action:* Tell the devs to add the `@trace` decorator to critical functions: `FilterPipeline.run`, `TradingAgent.decide`, and `YFinanceCollector.get_data`.

#### Phase 3: Raw Context Snapshots (File System)

Sometimes the database truncates data, or you want to see the raw HTML/Markdown scraped from news sites before the bot processed it.

**Ticket 6: Build `LocalArtifactLogger`**
Create `app/services/artifact_logger.py`:

* Every time the bot runs a cycle, create a local folder: `logs/runs/YYYY-MM-DD_HH-MM/{ticker}/`.
* Dump the exact JSON responses from Yahoo Finance, the exact text scraped from News, and the final LLM prompt into local `.json` and `.md` files in this folder.
* *Why?* If the bot makes a terrible trade on a Friday, you can open the folder for that Friday and look at the exact files it ingested, making it incredibly easy to see if bad Yahoo Finance data caused the bad trade.

### Acceptance Criteria for the Dev Team

1. **Air-Gapped:** Disconnect the machine from the internet (except for scraping stock data/news). The observability UI and logging must continue to work perfectly.
2. **Persistence:** If the bot buys `$AAPL`, I must be able to open my frontend, click AAPL, and read the exact text the LLM output to justify the trade.
3. **Traceability:** Developers must be able to open `http://localhost:6006` and see a waterfall timeline of how long data fetching took vs. how long Ollama took to think.
