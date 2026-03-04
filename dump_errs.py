"""Extract Execution Errors."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import duckdb

db = duckdb.connect("d:/Github/Lazy-Trading-Bot/data/trading_bot.duckdb", read_only=True)
errs = db.execute("SELECT id, decision_id, ts, broker_error FROM trade_executions WHERE broker_error != ''").fetchall()
if not errs:
    print("No errors found.")
for e in errs:
    print(f"[{e[2]}] Execution {e[0][:8]} (Decision {e[1][:8]}): {e[3]}")
db.close()
