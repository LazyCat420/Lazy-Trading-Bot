"""Check Constraints."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import duckdb

db = duckdb.connect("d:/Github/Lazy-Trading-Bot/data/trading_bot.duckdb", read_only=True)

try:
    print("Checking constraints on 'positions' table...")
    res = db.execute(
        "SELECT constraint_type, constraint_text "
        "FROM duckdb_constraints() "
        "WHERE table_name = 'positions'"
    ).fetchall()
    
    for r in res:
        print(r)
except Exception as e:
    print(f"Error: {e}")

db.close()
