Here is the full plan you can copy and paste to your dev team:

***

# Feature Plan: Enhanced Holdings & Congress Viewer in Data Explorer

## 1. Overview
The current flat-table view for 13F Filings and Congress trades is too hard to read when you have thousands of holdings across dozens of funds. We need to redesign these two tabs using a **master-detail accordion layout** combined with a **holdings history timeline and PnL calculations** so the user can visually track what every fund or politician owns, when they bought or sold, and how those positions are performing.

***

## 2. Hedge Fund Holdings Tab — New Layout

### A. Master-Detail Accordion Design
Replace the flat spreadsheet with a two-level layout:

- **Level 1 (Fund Card/Row):** Each fund (Berkshire Hathaway, Citadel, Renaissance, etc.) gets its own collapsible card at the top level. The card header should show the Fund Name, the most recent filing quarter, total number of holdings, and total portfolio market value.
- **Level 2 (Holdings Table):** When the user clicks/expands a fund card, the holdings table for that fund slides open directly underneath it. This keeps everything in context without navigating away from the page.
- The user should be able to expand multiple fund cards simultaneously to compare holdings side-by-side.

### B. Holdings Table Columns (Inside Each Fund Card)
Each expanded fund should show a detailed holdings table with the following columns:

- **Ticker Symbol** (bold, clickable to open a chart or detail panel)
- **Company Name**
- **Shares Held**
- **Market Value ($)**
- **% of Portfolio** (shown as a mini progress bar or number)
- **Quarter-over-Quarter Change** — a badge showing one of four states: `NEW` (green), `ADDED` (light green), `REDUCED` (orange), `SOLD OUT` (red)
- **Avg Cost Basis** (if calculable from filing history)
- **Current Price** (pulled from a live or daily-cached price feed)
- **Estimated PnL ($)** — calculated as `(Current Price - Avg Cost Basis) x Shares Held`
- **Estimated PnL (%)** — shown in green if positive, red if negative
- **First Reported Quarter** — the first time this ticker appeared in their 13F

### C. History Timeline View (Per Holding)
When the user clicks on a specific ticker row inside a fund's holdings, a **side panel or modal** should slide open showing the full history of that fund's position in that stock across all stored quarters. This should display as a timeline/chart with:

- A line chart showing **shares held over time** (one data point per quarter)
- A line chart showing **market value over time**
- A table below the chart listing every quarter they reported this holding, the shares, value, and the calculated change from the prior quarter

***

## 3. Congress Trades Tab — New Layout

### A. Same Accordion Design, Grouped by Politician
- **Level 1 (Politician Card):** Each politician gets a collapsible card showing their name, party affiliation (with a color indicator — blue/red), chamber (House/Senate), state, and total number of trades reported.
- **Level 2 (Trades Table):** Expands to show all their individual trade reports.

### B. Trade Table Columns (Inside Each Politician Card)
- **Transaction Date**
- **Ticker Symbol**
- **Company Name**
- **Transaction Type** — `BUY` (green badge) or `SELL` (red badge)
- **Amount Range** (e.g., $15k–$50k)
- **Days to Report** — calculated as the number of days between Transaction Date and Reporting Date (important because legally they have 45 days to report — a short lag is suspicious and interesting to traders)
- **Current Price**
- **Estimated Value at Time of Trade** (midpoint of the reported range)
- **Estimated PnL ($)** — calculated from estimated purchase price to current price
- **Estimated PnL (%)** — green/red color coded

### C. Performance Summary Card (Top of Politician's Card Header)
Each politician's collapsed card header should also show a quick summary stat like:
- Total Buys vs. Sells this year
- Win Rate % (what percentage of their buys are currently in profit based on estimated entry)
- Best performing trade ticker

***

## 4. PnL Calculation Strategy (For the Dev Team)
Since 13F filings do not report exact cost basis, and Congress reports only give an amount range, the team should use these estimation methods:

- **13F Hedge Fund Cost Basis:** Use the quarter-end price (available from a historical price API like Polygon.io or yFinance) on the date the filing was submitted as the estimated average cost basis for NEW positions. For ADDED positions, weight-average the new shares at current quarter price against the existing position.
- **Congress Trade Cost Basis:** Use the midpoint of the reported dollar range (e.g., $15k–$50k → use $32.5k) divided by the stock price on the transaction date to estimate shares. Then calculate PnL from there.
- **All PnL figures must be clearly labeled "Estimated"** in the UI so users understand these are approximations, not exact broker data.
- Cache daily prices so PnL calculations do not require a live API call every time a user opens the page.

***

## 5. Additional UI Suggestions

- **Fund/Politician Filter Bar:** At the top of both tabs, add a quick-filter pill bar to show only specific funds or politicians rather than scrolling through all of them.
- **Sort Cards By:** Allow sorting the top-level fund/politician cards by Total Portfolio Value, Number of Trades, Best PnL, or Most Recent Activity.
- **"Overlap" Feature (Bonus):** A small button that shows which tickers are held by multiple hedge funds simultaneously — a classic signal that institutional consensus is building on a stock.
- **Color Coded Party/Fund Branding:** Use subtle left-border accent colors on politician cards (blue for Democrat, red for Republican) and on fund cards use a small avatar or logo if available.

***

## 6. Developer Checklist
- [ ] Redesign 13F Filings tab using accordion master-detail layout grouped by Fund
- [ ] Redesign Congress tab using accordion master-detail layout grouped by Politician
- [ ] Build holdings history timeline chart component (per ticker per fund)
- [ ] Build PnL calculation service in the backend using historical price data
- [ ] Add "Days to Report" calculated column to Congress trades
- [ ] Add politician performance summary stats to card headers
- [ ] Add fund-level filter/sort bar at the top of both tabs
- [ ] Clearly label all PnL figures as "Estimated" throughout the UI
- [ ] Add "Overlap" feature showing tickers held by multiple funds simultaneously
- [ ] Cache daily prices server-side so PnL does not require a live call on every page load