"""Schema DB Checker."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import duckdb

db = duckdb.connect("d:/Github/Lazy-Trading-Bot/data/trading_bot.duckdb", read_only=True)

with open("d:/Github/Lazy-Trading-Bot/schema_out.txt", "w", encoding="utf-8") as f:
    try:
        tables = db.execute("SHOW TABLES").fetchall()
        f.write("Checking All Table Primary Keys:\n")
        for t in tables:
            t_name = t[0]
            cols = db.execute(f"SELECT column_name FROM information_schema.key_column_usage WHERE table_name = '{t_name}'").fetchall()
            pk_names = [c[0] for c in cols]
            
            # If the PK is exactly ONLY ['ticker']
            if pk_names == ['ticker']:
                f.write(f"  [SINGLE TICKER PK] {t_name}\n")
            elif 'ticker' in pk_names:
                f.write(f"  [COMPOSITE/OTHER PK] {t_name}: {pk_names}\n")
        
    except Exception as e:
        f.write(f"Error: {e}\n")
db.close()
