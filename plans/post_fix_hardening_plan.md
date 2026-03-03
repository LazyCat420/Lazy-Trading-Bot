# Post-Fix Performance & Risk Hardening Plan

Now that the core trading bugs (PK conflicts, empty cash crashes) are fixed, the pipeline can successfully execute end-to-end. The next evolutionary step is taking the bot from "functional" to "safe, profitable, and observable."

This plan covers the three core epics required to make the bot robust enough for paper trading and eventual live capital.

---

## Epic 1: Local OpenTelemetry & Auditing (Observability)
*Problem: When the bot makes a bad trade, we currently have no easy way to see exactly what news or data the LLM read to reach that conclusion.*

**1. The "Thought Ledger" (DuckDB Logging)**
- Create a new `llm_audit_logs` table in `app/database.py`.
- Intercept all calls in `LLMService` to log the exact `system_prompt`, `user_context`, and `raw_response`.
- *Goal:* Allow developers to query exactly what the LLM was fed vs. what it hallucinated.

**2. Local OpenTelemetry (Arize Phoenix)**
- Install `arize-phoenix`, `arize-otel`, and `openinference-instrumentation`.
- Add decorators to trace the execution waterfall (`FilterPipeline` -> `Collector` -> `LLMService`).
- Boot the local Phoenix UI (`localhost:6006`) on startup so developers can visually inspect execution times and token counts without any data leaving the machine.

---

## Epic 2: Mathematical Risk & Execution Guardrails
*Problem: LLMs are notoriously bad at math. The LLM should dictate "Direction" (Buy/Sell) and "Conviction", but deterministic Python code must handle the sizing and risk.*

**1. Deterministic Position Sizing**
- Build `app/services/risk_manager.py`.
- When the LLM outputs a `BUY` action, pass it to the risk manager.
- The risk manager calculates the exact share quantity based on a hardcoded max risk (e.g., never risk more than 2% of total portfolio cash on a single trade).

**2. Hardcoded Stop-Loss & Take-Profit**
- Do not let the LLM guess a stop-loss price. 
- Implement an automated trailing stop-loss (e.g., -5% or an ATR-based calculation) inside `ExecutionService` that is attached to every `BUY` order.

**3. Portfolio Kill Switch (Circuit Breaker)**
- Add a daily drawdown checker. If the portfolio drops by >X% in a 24-hour period, flip `LIVE_TRADING = False` and halt all new operations.

---

## Epic 3: AI Prompt & Reasoning Tuning
*Problem: Feeding raw scraped news directly into the LLM can cause "Lost in the Middle" syndrome, where the AI ignores critical data.*

**1. Context Window Optimization**
- Based on the Phoenix logs, identify the average token count of the `user_context`.
- If news articles are pushing the prompt past 4,000 tokens, implement a pre-summarization step to compress the news before feeding it to the final `TradingAgent`.

**2. Anti-Hallucination Prompting**
- Update the `TradingAgent` system prompt to strictly enforce grounding: *"You may only cite numbers and facts explicitly provided in the user context. Do not use outside knowledge. If the context does not support a trade, output HOLD."*

---

## Acceptance Criteria for the Dev Team
1. **Auditability:** Developers can open `localhost:6006` (offline) and see the exact milliseconds and context of a trade decision.
2. **Safety:** A unit test proves that if the LLM requests a trade size that exceeds 10% of the portfolio, the `risk_manager` overrides it and caps it at 2%.
3. **Resilience:** The bot runs a full cycle on 10 tickers without exceeding the LLM context window limits.