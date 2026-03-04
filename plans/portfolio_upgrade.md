# Portfolio UI Upgrades: Finviz Links & Deep Data Accordions

## Objective
Enhance the "Open Positions" view in the Portfolio tab so users can quickly verify charts on Finviz and dive into the underlying data/rationale for each trade without leaving the bot's interface.

---

## Ticket 1: Finviz Hyperlinks for Tickers
**Goal:** Make every ticker in the Open Positions list a clickable link to Finviz.

*   **Frontend Action (`app/static/terminal_app.js` or equivalent frontend logic):**
    *   Locate the JS function that renders the open position rows.
    *   Change the plain text rendering of the ticker to an anchor tag:
        ```html
        <a href="https://finviz.com/quote.ashx?t=${position.ticker}" target="_blank" class="finviz-link">${position.ticker}</a>
        ```
    *   **CSS:** Add styling for `.finviz-link` (e.g., standard terminal green/blue, `text-decoration: underline` on hover) so it blends with the terminal theme but clearly indicates interactivity.

---

## Ticket 2: Backend Data Enrichment for Positions
**Goal:** Ensure the backend portfolio endpoint returns all the "useful data" needed for the collapsible menu so the frontend doesn't have to make secondary fetch requests.

*   **Backend Action (`app/main.py` routes & `app/services/paper_trader.py`):**
    *   Update the endpoint that serves the Portfolio data (likely `/api/portfolio` or `/api/positions`).
    *   When fetching open positions from the DuckDB `positions` table, do a `LEFT JOIN` or secondary query on the `trade_decisions` or `llm_audit_logs` tables to pull the latest context for that specific ticker and `bot_id`.
    *   Enrich the API JSON payload to include an object of `details`. For example:
        *   `rationale`: The LLM's reasoning for the BUY.
        *   `confidence`: The confidence score.
        *   `technicals`: RSI, MACD, Volume (if stored).
        *   `entry_date`, `stop_loss`, `take_profit` (if applicable).

---

## Ticket 3: Collapsible Details Menu (The Accordion)
**Goal:** Add a toggleable detail pane under each stock row to display the enriched data.

*   **Frontend Action (`app/static/terminal_app.js`):**
    *   Modify the table/list generation to append an extra "detail row" or `div` right after the main position row. 
    *   Give it a class like `.position-details` and set default CSS to `display: none;`.
    *   Add an expand/collapse toggle button (e.g., `[+]` or `▼`) to the main row next to the ticker or at the far right.
    *   **JS Logic:** Add an event listener to the toggle button. When clicked:
        1. Swap the icon to `[-]` or `▲`.
        2. Toggle the `display` property of the adjacent `.position-details` container.
    *   **Inner Layout:** Format the detail row as a neat, terminal-styled pre-formatted text box or a CSS grid displaying the enriched backend data (Rationale, Technicals, Risk metrics).

---

## Acceptance Criteria
1. Clicking any ticker symbol in the Portfolio tab opens a new browser tab straight to its Finviz quote page.
2. Clicking the `[+]` toggle on a position smoothly expands a panel showing the LLM's exact rationale and collected data for that trade.
3. Clicking the toggle again collapses the panel cleanly without breaking the UI layout.
4. The expanded data accurately reflects the historical decision data stored in DuckDB.