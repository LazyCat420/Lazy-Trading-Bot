Here is the updated, complete project plan and checklist ready to be copied and pasted to your dev team. It incorporates the ability to completely delete garbage/blank data and fix incorrectly scraped items, while maintaining a clean, high-performance UI.

***

# Project Plan: Data Explorer UI & Database Manager

## 1. Overview & Core Objectives
We need to build a new **"Data"** tab in the left-hand sidebar navigation of the frontend. This will act as a comprehensive database viewer and manager. The goal is to allow users to inspect all data scraped by the Python backend (Reddit, YouTube, SEC 13F, Congress), verify its accuracy, manually fix errors, and permanently delete rows if a scraper pulled blank or corrupted data. 

## 2. General UI Layout & Architecture
*   **Navigation:** Add a "Data" link in the left sidebar with a standard database icon.
*   **Page Structure:** The page should be full-width and full-height to maximize screen real estate for large spreadsheets.
*   **Top Tab Bar:** Directly below the page header, create horizontal tabs to switch between the different data domains: 
    *   `[YouTube]` `[Reddit]` `[13F Filings]` `[Hedge Fund Holdings]` `[Congress]`
*   **Global Action Bar:** A sticky toolbar just above the data grid containing:
    *   **Global Search:** To find specific tickers, funds, or keywords.
    *   **Date Picker:** To filter rows by when they were scraped.
    *   **Refresh Data:** A button to pull the latest database entries.
    *   **"Clean Blank Data" Button:** A bulk-action button that automatically scans the current view and deletes rows where key fields (like Ticker, Text, or Value) are entirely blank.

## 3. Data Grid UI Standards (The "Spreadsheet" Component)
The devs should use a high-performance grid component (like AG Grid, MUI DataGrid, or TanStack Table) configured with the following industry-standard features:
*   **Sticky Headers & Frozen Columns:** The header row must stay at the top when scrolling down. The first identifying column (e.g., Ticker or Fund Name) must freeze on the left when scrolling horizontally.
*   **Sorting & Resizing:** Every column header must be clickable to sort (A-Z, High-Low, New-Old) and draggable to resize or reorder.
*   **Pagination:** Implement server-side pagination (e.g., 50 or 100 rows per page) so the browser doesn't crash when loading massive tables (like Renaissance Technologies' 2,166 holdings).
*   **Dark Mode Styling:** Ensure the grid matches our current theme, using subtle alternating row colors (zebra striping) to make wide rows easy to read.

## 4. Data Management: Editing & Deleting
Since scrapers occasionally grab empty pages or parse things incorrectly, the user must have full CRUD (Create, Read, Update, Delete) control over the tables.

*   **Permanent Deletion (Trash):** 
    *   Add an "Actions" column pinned to the far right of the table.
    *   Include a prominent "Trash/Delete" icon. Clicking this should permanently remove the row from the database (hard delete) to clear out bad/blank scrapes.
    *   Add checkboxes on the left side of every row to allow bulk-deleting multiple bad rows at once.
*   **Inline Editing:**
    *   If a row has a minor error (e.g., the LLM hallucinated a wrong ticker or misspelled a name), the user should be able to double-click the specific cell, type the correction, and hit Enter to save it directly to the database.
*   **Status Indicators:**
    *   Use color-coded badges for statuses (e.g., a green "Scanned" badge vs. a yellow "Un-scanned" badge for YouTube transcripts).

## 5. Tab-by-Tab Column Configuration

### Tab 1: YouTube Data
*   **Columns:** ID | Scrape Date | Channel Name | Video Title | Scrape Status (Un-scanned / LLM Scanned) | Tickers Found | Transcript Snippet | Actions (Edit/Delete)

### Tab 2: Reddit Data
*   **Columns:** ID | Scrape Date | Subreddit | Thread Title | Category (Priority / Rising Trend) | Tickers Mentioned | Thread URL | Actions (Edit/Delete)

### Tab 3: 13F Filings (High-Level Fund View)
*   **Columns:** ID | Scrape Date | Fund Name (e.g., Citadel, Bridgewater) | CIK Number | Filing Quarter (e.g., 2025Q4) | Total Holdings Count | Actions (Delete)

### Tab 4: Hedge Fund Holdings (Deep-Dive View)
*   *Note: Include a dropdown filter in the toolbar to select a specific Fund or Quarter.*
*   **Columns:** ID | Fund Name | Quarter | Ticker Symbol (Bold) | Company Name | Total Shares | Market Value ($) | % of Portfolio | Actions (Edit/Delete)

### Tab 5: Congress Trades
*   **Columns:** ID | Transaction Date | Politician Name | Ticker | Asset Type | Transaction Type (Buy/Sell color-coded) | Amount Range | Actions (Edit/Delete)

## 6. Developer Action Items / Checklist
- [ ] Set up the UI skeleton: Left nav item, page wrapper, and the 5 top-level tabs.
- [ ] Implement the high-performance data grid component.
- [ ] Build backend API routes (GET) to fetch data for each tab with pagination and search functionality.
- [ ] Build backend API routes (DELETE) to handle individual and bulk row deletions.
- [ ] Build backend API routes (PUT/PATCH) to handle inline cell edits.
- [ ] Add the "Clean Blank Data" utility to easily sweep out failed scrapes.
- [ ] Style the grid to match the bot's dark theme, ensuring Buy/Sell and status indicators use clear color coding.

*** 

### 💡 Extra Suggestion for the Dev Team:
For the Reddit and YouTube tabs, consider adding a feature where clicking the row opens a modal showing the raw text/transcript the bot scraped. This will help the user quickly see *why* a scrape failed and easily decide if they should delete the row.