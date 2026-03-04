"""Duplicate watchlist."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import duckdb

db = duckdb.connect("d:/Github/Lazy-Trading-Bot/data/trading_bot.duckdb")
try:
    print("Testing Watchlist constraint...")
    try:
        db.execute("INSERT INTO watchlist (ticker, bot_id) VALUES ('KO', 'default')")
        print("Inserted KO successfully.")
    except Exception as e:
        print(f"Error 1: {e}")

    try:
        db.execute("INSERT INTO watchlist (ticker, bot_id) VALUES ('KO', 'default')")
        print("Inserted KO successfully again.")
    except Exception as e:
        print(f"Error 2: {e}")

    print("Testing Ticker Scores constraint...")
    try:
        db.execute("INSERT INTO ticker_scores (ticker) VALUES ('KO')")
        print("Inserted KO successfully.")
    except Exception as e:
        print(f"Error 1: {e}")
        
    try:
        db.execute("INSERT INTO ticker_scores (ticker) VALUES ('KO')")
        print("Inserted KO successfully again.")
    except Exception as e:
        print(f"Error 2: {e}")
except Exception as e:
    pass

db.close()
