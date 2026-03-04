# UI Upgrades: Internal Stock Terminal & Deep Data Accordions

## Objective
Enhance the bot's interface so users can click any ticker (in Portfolio or the Bot Leaderboard) to open an internal "Stock Terminal." This terminal will display the local data the bot scraped (charts, technicals, news, and LLM rationale) while providing external shortcut links to Finviz and Yahoo Finance. Additionally, implement a collapsible data accordion in the Portfolio view for quick context.

---

## Ticket 1: The Internal "Stock Terminal" View
**Goal:** Create a dedicated view/modal that acts as the "source of truth" for what the bot knows about a specific stock.

*   **Frontend Action (`app/static/terminal_app.js`):**
    *   Create a new modal or a slide-out panel (e.g., `#stock-terminal-modal`).
    *   **Header:** Display the Ticker Symbol prominently. Next to it, add external icon links:
        *   `[Finviz]` -> `https://finviz.com/quote.ashx?t=${ticker}`
        *   `[Yahoo]` -> `https://finance.yahoo.com/quote/${ticker}`
    *   **Body layout (Grid):**
        *   *Top Section:* Price chart (using a lightweight library like Lightweight Charts or simply displaying the raw OHLCV data array the bot pulled).
        *   *Middle Section:* Technical and Fundamental data table (RSI, MACD, Volume, etc., pulled straight from DuckDB).
        *   *Bottom Section:* "Bot Memory" - A scrolling text box showing the scraped news articles and the LLM's summarized rationale.

*   **Backend Action (`app/main.py`):**
    *   Create a new endpoint: `GET /api/stock-terminal/{bot_id}/{ticker}`
    *   This endpoint aggregates data from `positions`, `trade_decisions`, and `llm_audit_logs` to feed the frontend modal.

---

## Ticket 2: Global Ticker Click Handlers
**Goal:** Ensure every ticker symbol across the app routes to the new Stock Terminal.

*   **Frontend Action (`app/static/terminal_app.js`):**
    *   **Portfolio View:** Convert all plain-text tickers in "Open Positions" to styled clickable links (`<a class="internal-ticker-link" data-ticker="${position.ticker}">...</a>`).
    *   **Bot Leaderboard View:** Convert all plain-text tickers in the dropdowns (Scoreboard/Watchlist) to the same clickable link format.
    *   **Event Listener:** Bind a global click handler to `.internal-ticker-link` that prevents default navigation, extracts the `data-ticker`, and opens the Stock Terminal Modal (fetching data from the Ticket 1 endpoint).

---

## Ticket 3: Collapsible Details Menu (The Portfolio Accordion)
**Goal:** Add a toggleable detail pane under each stock row in the Portfolio so users don't *have* to open the full terminal for a quick glance.

*   **Frontend Action (`app/static/terminal_app.js`):**
    *   Modify the Portfolio table/list generation to append a hidden "detail row" (`<div class="position-details" style="display: none;">`).
    *   Add an expand/collapse toggle button (e.g., `[+]` or `▼`) to the main row.
    *   **JS Logic:** On click, toggle the display of the detail row.
    *   **Content:** Display a miniaturized version of the bot's rationale and confidence score. (Data provided via the standard Portfolio API payload).

---

## Acceptance Criteria
1. Clicking any ticker symbol in the Portfolio tab OR the Bot Leaderboard opens the internal Stock Terminal modal.
2. The Stock Terminal displays local database data (price, technicals, scraped news) and the LLM's thought process.
3. The top of the Stock Terminal contains working external links to Finviz and Yahoo Finance.
4. Clicking the `[+]` toggle on a Portfolio position smoothly expands a quick-glance panel showing the LLM's rationale.