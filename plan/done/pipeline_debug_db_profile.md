# Pipeline Debug Infrastructure — Database Profile Switching

The overnight pipeline run took **5+ hours** for 80 tickers and stalled during the trading phase due to Prism LLM connection timeouts (`CONN_ERROR → ReadError`). To iterate faster on debugging, this plan adds a **database profile switching** mechanism that lets you swap between a production DB with all your data and a tiny test DB with just 1 ticker.

## Root Cause: Why the Pipeline is Slow

From the terminal logs:

```
Prism request CONN_ERROR -> 56.2s: ReadError: (no message)
[LLM] Empty response with format=json — retrying with format=text + JSON instructions
```

The **DeepAnalysis phase is actually fast** — it's pure Python math (zero LLM calls). The slowdown is in the **Trading phase** (`TradingPipelineService`), where each ticker triggers:
1. An LLM call to `TradingAgent.decide()` via Prism → Ollama
2. Prism's connection to Ollama drops after ~56s (likely Ollama is swapping models or GPU context is exhausted)
3. The dual-mode retry fires another request, doubling the time
4. With 78 tickers × ~3-4 min each → **4-5 hours** in trading alone

> [!IMPORTANT]
> The DB switching feature below lets you test with just 1 ticker so you can isolate and fix the Prism timeout issue without waiting hours per iteration.

## Proposed Changes

### Config

#### [MODIFY] [config.py](file:///home/braindead/github/Lazy-Trading-Bot/app/config.py)

- Add `DB_PROFILE` setting (default `"main"`) — values: `"main"` or `"test"`
- The DB path changes based on profile:
  - `main` → `data/trading_bot.duckdb` (existing, unchanged)
  - `test` → `data/trading_bot_test.duckdb` (isolated, single-ticker)
- Add profile to persistence (`_apply_llm_config`, `update_llm_config`, `get_llm_config`)

---

### Database

#### [MODIFY] [database.py](file:///home/braindead/github/Lazy-Trading-Bot/app/database.py)

- Make `get_db()` respect `settings.DB_PROFILE` → derives path automatically
- Add `switch_db(profile: str)` — closes the existing DuckDB connection, updates `settings.DB_PROFILE`, and on next `get_db()` call, opens the new database
- Add `get_current_profile()` — returns which DB is active

---

### API Endpoints

#### [MODIFY] [main.py](file:///home/braindead/github/Lazy-Trading-Bot/app/main.py)

- `GET /api/settings/db-profile` — returns `{"profile": "main"|"test", "db_path": "..."}`
- `POST /api/settings/db-profile` — body `{"profile": "test"}` → calls `switch_db()`, returns new status

---

### Test Seed Script

#### [NEW] [seed_test_db.py](file:///home/braindead/github/Lazy-Trading-Bot/scripts/seed_test_db.py)

Standalone script that:
1. Opens `data/trading_bot_test.duckdb` directly
2. Initializes all tables (reuses `_init_tables`)
3. Seeds exactly **1 ticker** (`AAPL`) with minimal data:
   - 30 days of price history
   - 1 fundamentals snapshot
   - 1 news article
   - 1 YouTube transcript
   - 1 watchlist entry (status=active)
4. This lets you run the full pipeline end-to-end with minimal data

---

### Unit Test

#### [NEW] [test_db_profile.py](file:///home/braindead/github/Lazy-Trading-Bot/tests/test_db_profile.py)

Tests:
- `test_default_profile_is_main` — settings starts as `"main"`
- `test_switch_to_test_profile` — `switch_db("test")` changes path and reconnects
- `test_switch_back_to_main` — switching back restores original path
- `test_profile_persists_in_config` — after update, config file reflects new profile
- `test_data_isolation` — writing to test DB doesn't appear in main DB

## Verification Plan

### Automated Tests

```bash
# Run the DB profile switching test
cd /home/braindead/github/Lazy-Trading-Bot
source venv/bin/activate
python -m pytest tests/test_db_profile.py -v
```

### Manual Verification

1. Start the server: `bash run.sh`
2. Check current profile:
   ```bash
   curl http://localhost:8000/api/settings/db-profile
   ```
   → should return `{"profile": "main", "db_path": "data/trading_bot.duckdb"}`
3. Switch to test profile:
   ```bash
   curl -X POST http://localhost:8000/api/settings/db-profile -H 'Content-Type: application/json' -d '{"profile": "test"}'
   ```
   → should return `{"profile": "test", "db_path": "data/trading_bot_test.duckdb"}`
4. Seed the test DB: `python scripts/seed_test_db.py`
5. Run the pipeline with just 1 ticker in the test DB to verify fast iteration
6. Switch back: `curl -X POST ... -d '{"profile": "main"}'`
7. Verify the main DB data is untouched
