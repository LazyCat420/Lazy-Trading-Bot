# Fix: Trading Loop Producing Empty Results

## Root Cause Analysis

### 3 bugs found:

1. **Bot fingerprint migration bug** (`botRegistry.js:77`)
   - `findByModelAndSettings()` had `return null` fallback for legacy bots with no fingerprint
   - This created a **new empty bot** (bot_53a43347) instead of reusing the existing one (bot_2239bf95)
   - New bot had no watchlist → analysis/trading phases analyzed 0 tickers

2. **Discovery limits too aggressive** (set to 1 in all files)
   - 1 reddit thread scraped → 0 extractable tickers → 0 discovery results
   - Pipeline: 0 discovered → 0 collected → 0 imported → 0 analyzed → 0 traded

3. **Audit tested wrong database** (`full_pipeline_audit.py`)
   - Node.js loop writes to MongoDB, but audit only checks DuckDB
   - "No changes detected" was correct — DuckDB wasn't modified

## Fixes Applied

- `botRegistry.js`: Legacy fallback now queries by model_name + backfills fingerprint
- All collection limits: 1 → 3 (threads, posts, comments, tickers, YouTube)
- Created `tests/phase_diagnostic.js`: MongoDB-aware test with --before/--after modes
- Deleted orphan bot_53a43347 from MongoDB
