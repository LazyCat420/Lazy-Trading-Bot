"""Test Duplicate."""
import duckdb

db = duckdb.connect("d:/Github/Lazy-Trading-Bot/data/trading_bot.duckdb")

try:
    # First, let's see if we have an old 'KO' position
    pos = db.execute("SELECT * FROM positions WHERE ticker = 'KO'").fetchall()
    print("Positions for KO:", pos)

    try:
        db.execute("INSERT INTO positions (ticker, bot_id) VALUES ('KO', 'default')")
        print("Inserted KO default successfully.")
    except Exception as e:
        print(f"Error inserting KO default: {e}")

    try:
        db.execute("INSERT INTO positions (ticker, bot_id) VALUES ('KO', 'other')")
        print("Inserted KO other successfully.")
    except Exception as e:
        print(f"Error inserting KO other: {e}")
except Exception as e:
    print(f"Fatal check failure: {e}")

db.close()
