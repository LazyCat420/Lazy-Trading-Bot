"""DB Schema Inspector."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import duckdb

db = duckdb.connect("d:/Github/Lazy-Trading-Bot/data/trading_bot.duckdb", read_only=True)

print("--- POSITIONS ---")
try:
    cols = db.execute("SELECT column_name FROM information_schema.key_column_usage WHERE table_name = 'positions'").fetchall()
    print("PK cols:", [c[0] for c in cols])
except Exception as e:
    print(f"Error: {e}")

print("--- TRADE_EXECUTIONS ---")
try:
    cols = db.execute("SELECT column_name FROM information_schema.key_column_usage WHERE table_name = 'trade_executions'").fetchall()
    print("PK cols:", [c[0] for c in cols])
except Exception as e:
    print(f"Error: {e}")
    
db.close()
