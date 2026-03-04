"""Schema DB Checker."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import duckdb

db = duckdb.connect("d:/Github/Lazy-Trading-Bot/data/trading_bot.duckdb", read_only=True)

try:
    tables = db.execute("SHOW TABLES").fetchall()
    print("Checking All Table Primary Keys:")
    for t in tables:
        t_name = t[0]
        cols = db.execute(f"SELECT column_name FROM information_schema.key_column_usage WHERE table_name = '{t_name}'").fetchall()
        pk_names = [c[0] for c in cols]
        
        # If the PK is exactly ONLY ['ticker']
        if pk_names == ['ticker']:
            print(f"  [SINGLE TICKER PK] {t_name}")
        elif 'ticker' in pk_names:
            print(f"  [COMPOSITE/OTHER PK] {t_name}: {pk_names}")
    
except Exception as e:
    print(f"Error: {e}")
db.close()
