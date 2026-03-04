"""Test INSERT OR REPLACE bug."""
import duckdb

db = duckdb.connect("d:/Github/Lazy-Trading-Bot/data/trading_bot.duckdb")
try:
    print("Testing INSERT OR REPLACE...")
    
    # Clean up test
    db.execute("DELETE FROM positions WHERE ticker = 'ZZZ'")
    
    # Insert once
    db.execute("INSERT INTO positions (ticker, qty, avg_entry_price, bot_id) VALUES ('ZZZ', 10, 100, 'default')")
    
    # Insert or Replace
    try:
        db.execute("INSERT OR REPLACE INTO positions (ticker, qty, avg_entry_price, bot_id) VALUES ('ZZZ', 10, 100, 'default')")
        print("Success!")
    except Exception as e:
        print(f"Error: {e}")
except Exception as e:
    pass
db.close()
