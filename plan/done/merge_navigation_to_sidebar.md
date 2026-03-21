# Merge Top Tabs Into Left Sidebar Navigation

## Problem
Dual navigation: TopBar (4 top tabs via state) + SidebarLayout (6 nav links via React Router). Sidebar links don't work because App switches by state, not routes.

## Fix
1. Remove `TopBar`, `CommandCenterTab`, `DataHubTab` wrappers
2. Move App root to single `HashRouter` + `Routes` inside `SidebarLayout`
3. Add Live Feed + Data Ingestion to sidebar nav
4. Remove per-page `HashRouter` and `SidebarLayout` wrappers
5. Derive active sidebar item from `useLocation()` instead of prop

## Files
- `frontend/static/terminal_app.js` — all changes
- `frontend/static/style.css` — if needed for sidebar styles
