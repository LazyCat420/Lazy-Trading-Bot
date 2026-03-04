"""Get broker error trace."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import duckdb

db = duckdb.connect("d:/Github/Lazy-Trading-Bot/data/trading_bot.duckdb", read_only=True)
err = db.execute("SELECT broker_error FROM trade_executions WHERE broker_error LIKE '%Duplicate key%' LIMIT 1").fetchone()
if err:
    print(err[0])
db.close()
