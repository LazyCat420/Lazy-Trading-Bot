# Implementation Plan: 55-Tool Pipeline Audit & Comprehensive Logging

This plan breaks down the systematic testing of all 55 python `app/services/` tools to ensure data accuracy, proper calculations, and correct data persistence inside the local DuckDB instance.

## Why Do We Have 55 Tools?
Before we audit them, it's important to clarify **why** the pipeline is so large. The 55 Python files exist because this bot is not a simple script; it is a **highly complex, autonomous multi-agent quantitative trading system**. We have broken it down into microservices for stability:

1. **Massive Data Scope:** You aren't just looking at prices. We have unique scrapers for Reddit sentiment, YouTube transcripts, Congress trades, SEC Hedge Fund 13Fs, and RSS News. Each has a dedicated collector tool.
2. **Deep Technical Math:** We don't rely only on LLMs. We have a pure Python `quant_engine.py` and `technical_service.py` that calculate 154+ technical indicators and distinct risk metrics before the LLM ever sees the data.
3. **Advanced AI Abstractions:** The LLM architecture is incredibly deep to prevent hallucinations. It involves a `TemplateRegistry`, a 3-phase `brain_loop`, tool-calling, Prompt Evolvers, and an Agentic Extractor.
4. **Deterministic Trading Safety:** We have hard-coded `circuit_breaker.py` and `risk_rules.py` layers to guarantee the bot cannot destroy capital even if the LLM breaks.
5. **Traceability:** We split responsibilities between distinct loggers for LLM calls (`llm_audit_logger`), trading decisions (`decision_logger`), pipeline events (`event_logger`), and RAG context (`artifact_logger`).

---

## Phase 1: Audit Data Collection (Sources)
We will write and run test scripts to interrogate DuckDB for output from the 7 data collectors to ensure they aren't passing `null` or silently failing.
- **Targets:** `yfinance`, `reddit`, `youtube`, `rss_news`, `sec_13f`, `congress`, `news`
- **Audit Method:** Fetch sample tickers (`SPY`, `NVDA`) directly via each service module through a new `test_auditors/` scratch folder. Ensure outputs are populated and types are correct.

## Phase 2: Audit Analysis & Quant Engines
We will verify that the mathematical representations passed to the LLM are accurate.
- **Targets:** `technical_service`, `risk_service`, `quant_engine`, `data_distiller`
- **Audit Method:** Take raw yfinance structural data and run it through `technical_service`. Assert that indicators like MACD and RSI fall within mathematically valid bounds ($0 \leq \text{RSI} \leq 100$). Check that the Distiller successfully compresses this into the LLM context limits.

## Phase 3: Audit LLM & RAG Infrastructure
Ensure our Prism Gateway routing and RAG context are fully functioning.
- **Targets:** `llm_service`, `embedding_service`, `retrieval_service`, `brain_loop`
- **Audit Method:** Inject fixed mock data into the pipeline and verify the `brain_loop` correctly extracts the facts via `retrieval_service`. Validate that `PromptEvolver` isn't corrupting the baked system prompts. Verify `ContextDisambiguator` correctly identifies messy symbols.

## Phase 4: Audit Trading, Risk & Execution
Validate the deterministic rule sets for capital safety.
- **Targets:** `risk_rules`, `circuit_breaker`, `execution_service`, `paper_trader`
- **Audit Method:** Feed the `paper_trader` simulated catastrophic LLM decisions (e.g. "Buy 1000% of Portfolio in Penny Stock X"). Verify the `risk_rules` and `execution_service` outright reject/clamp the trade, and `circuit_breaker` halts the loop when simulated portfolio drawdown exceeds predefined limits.

## Phase 5: Develop Comprehensive Logging System
To solve the issue of constantly chasing bugs across 55 files, we need a unified health dashboard.
- We will consolidate current distinct loggers (`PipelineTracer`, `PipelineHealth`, `EventLogger`, `ArtifactLogger`, `ws_broadcaster`) into a **Unified Telemetry System**.
- **Proposed System:**
  1. **Python Side (`UnifiedLogger.py`):** Wraps all tools. Every function call logs: `(Step Name, Input Size, Output Size, Duration, Success/Fail_Reason)`.
  2. **Database Side (`pipeline_telemetry` table in DuckDB):** Appends discrete runs with a `cycle_id` linking every single module's execution state.
  3. **Frontend Diagnostics Dashboard:** We will expand the Diagnostics page to include a "Data Pipeline Map" where each of the 55 nodes lights up Green/Red in real-time. If "SEC 13F" fails, you will see exactly that step fail in the UI with the exact exception, instead of a silent `null` at the end of the run.

---

> [!IMPORTANT]
> Because auditing 55 complex tools takes substantial time, we will approach this sequentially. First, I will build out Phase 1 testing and run the audits on the scrapers, and report back the health. Once we confirm they work (and fix what doesn't), we move to Phase 2. Is this plan approved?
