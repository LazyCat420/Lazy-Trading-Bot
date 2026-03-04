# Autobot Monitor UI Standardization: Unified Dossier Accordions

## Objective
Currently, the "Watchlist" uses a robust, tabbed/collapsible dossier system (showing Overview, News, YouTube, Fundamentals, Technicals, Risk, and Analysis) for each stock. The "Autobot Monitor" (Portfolio and Bot Leaderboard holdings) lacks this depth. 

This plan details how to refactor and reuse the existing Watchlist dossier UI components so that **any stock in the Autobot Monitor (Portfolio or Leaderboard)** displays the exact same rich, tabbed data accordion.

---

## Ticket 1: Extract and Componentize the Dossier UI
**Goal:** The current HTML/JS that builds the Watchlist collapsible dossier is likely hardcoded to the Watchlist rendering loop. It needs to be extracted into a reusable JavaScript function.

*   **Action (`app/static/terminal_app.js`):**
    *   Find the existing function that builds the Watchlist rows (e.g., `renderWatchlist` or the HTML template string with the `Overview | News | YouTube | ...` tabs).
    *   Extract the inner HTML generation into a standalone pure function: 
        `function buildDossierAccordionHTML(tickerData, uniqueElementId) { ... }`
    *   Ensure the tab-switching logic (event listeners for clicking "News", "Technicals", etc.) delegates events using a generic class/data-attribute system (like `data-dossier-target`) rather than hardcoded IDs, so multiple dossiers can be open on the same page without ID collisions.

---

## Ticket 2: Standardize the Backend Data Payload
**Goal:** The Portfolio and Bot Leaderboard API endpoints must return the same rich dataset that the Watchlist endpoint currently returns.

*   **Action (`app/main.py` & API Routes):**
    *   Inspect the existing `GET /api/watchlist` endpoint to see how it gathers `Overview`, `News`, `Fundamentals`, etc. (likely pulling from a `dossiers` or `deep_analysis` table).
    *   Update the endpoints powering the Autobot Monitor:
        *   `GET /api/portfolio` (or `GET /api/positions`)
        *   `GET /api/leaderboard` (the endpoint fetching bot holdings)
    *   Modify these endpoints so that for every position/holding, they do a database join (or secondary query) to attach the `dossier_data` object exactly as the Watchlist does. 

---

## Ticket 3: Integrate the Dossier into Autobot Monitor Views
**Goal:** Wire the newly componentized UI (Ticket 1) to the enriched data (Ticket 2) in the Autobot Monitor tabs.

*   **Action (`app/static/terminal_app.js`):**
    *   **Portfolio View:** When rendering the "Open Positions" table/list, append a hidden detail row below each position. Inject the output of `buildDossierAccordionHTML(positionData, position.id)` into this row.
    *   **Bot Leaderboard View:** When a user expands a bot to view its holdings, render each holding with the same hidden detail row and inject `buildDossierAccordionHTML(holdingData, holding.id)`.
    *   Add the standard `[+]` toggle button to the main row of both views to expand/collapse the dossier row.

---

## Acceptance Criteria
1. The code generating the 7-tab dossier (Overview, News, YouTube, etc.) is written exactly once in the codebase and reused everywhere.
2. Expanding a stock in the **Watchlist** looks identical to expanding a stock in the **Portfolio**.
3. Expanding a stock in a **Bot Leaderboard** dropdown looks identical to the Watchlist.
4. Clicking through the tabs (e.g., from Fundamentals to Technicals) in the Portfolio dossier works without breaking the UI or conflicting with other open dossiers on the page.