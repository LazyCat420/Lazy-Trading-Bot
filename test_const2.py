"""Find all UNIQUE constraints."""
import duckdb

db = duckdb.connect("d:/Github/Lazy-Trading-Bot/data/trading_bot.duckdb", read_only=True)
try:
    print("ALL CONSTRAINTS:")
    res = db.execute("SELECT table_name, constraint_type, constraint_text FROM duckdb_constraints()").fetchall()
    for r in res:
        if 'UNIQUE' in r[1]:
            print(r)
            
    print("\ALL INDEXES:")
    indexes = db.execute("SELECT index_name, sql FROM duckdb_indexes()").fetchall()
    for i in indexes:
        print(i)
except Exception as e:
    print(e)
db.close()
