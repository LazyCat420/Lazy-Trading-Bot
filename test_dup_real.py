"""Test Duplicate Real."""
import duckdb

db = duckdb.connect("d:/Github/Lazy-Trading-Bot/data/trading_bot.duckdb")

try:
    print("Testing Positions constraint...")
    try:
        db.execute(
            "INSERT INTO positions (ticker, qty, avg_entry_price, opened_at, last_updated, bot_id) "
            "VALUES ('KO', 10, 100.0, current_timestamp, current_timestamp, 'default')"
        )
        print("Inserted KO successfully.")
    except Exception as e:
        print(f"Error 1: {e}")

    try:
        db.execute(
            "INSERT INTO positions (ticker, qty, avg_entry_price, opened_at, last_updated, bot_id) "
            "VALUES ('KO', 20, 100.0, current_timestamp, current_timestamp, 'default')"
        )
        print("Inserted KO successfully again.")
    except Exception as e:
        print(f"Error 2: {e}")

    # Also let's list all indexes on positions
    indexes = db.execute(
        "SELECT index_name FROM duckdb_indexes() WHERE table_name = 'positions'"
    ).fetchall()
    print("Indexes on positions:", indexes)

except Exception as e:
    pass

db.close()
