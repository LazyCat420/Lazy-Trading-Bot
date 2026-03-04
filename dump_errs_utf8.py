"""Extract Execution Errors UTF8."""
import duckdb

db = duckdb.connect("d:/Github/Lazy-Trading-Bot/data/trading_bot.duckdb", read_only=True)
errs = db.execute("SELECT id, decision_id, ts, broker_error FROM trade_executions WHERE broker_error != ''").fetchall()

with open("d:/Github/Lazy-Trading-Bot/dump_errs_filtered.txt", "w", encoding="utf-8") as f:
    if not errs:
        f.write("No errors found.\n")
    for e in errs:
        f.write(f"[{e[2]}] Execution {e[0][:8]} (Decision {e[1][:8]}): {e[3]}\n")

db.close()
