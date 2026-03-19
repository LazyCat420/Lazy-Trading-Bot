# Autobot Monitor Page Fix Plan

## Issue
The Autobot Monitor page at `http://127.0.0.1:3000/#/monitor` crashes with the error: `Uncaught TypeError: (portfolio.positions || []).flatMap is not a function`.
This occurs because `portfolio.positions` is an object (dictionary) representing open positions, but the React frontend assumes it is an array and attempts to call `.flatMap()` on it.

## Root Cause
In `/home/braindead/development/Lazy-Trading-Bot/frontend/static/terminal_app.js` at line 5626, the code uses `...(portfolio.positions || []).flatMap(...)`. Since `portfolio.positions` is an object, the `|| []` fallback doesn't trigger, and an object has no `flatMap` method.

## Plan
1. Update `terminal_app.js` in `Lazy-Trading-Bot/frontend/static/` to properly handle `portfolio.positions` whether it's an array or an object. 
2. Change codebase logic at line 5626 from:
   `...(portfolio.positions || []).flatMap(...)`
   To:
   `...(Array.isArray(portfolio.positions) ? portfolio.positions : Object.values(portfolio.positions || {})).flatMap(...)`
3. Verify that the open positions count on line 5564 doesn't break. Change `portfolio.positions_count` to fallback to checking the object keys length if `positions_count` is undefined.
4. Move this plan to `plan/done/` after completion.
5. Append this fix summary to `CHANGES.md`.
