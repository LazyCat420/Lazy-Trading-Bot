"""Buy Execution Simulator."""
import sys
import uuid
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import duckdb

db = duckdb.connect("d:/Github/Lazy-Trading-Bot/data/trading_bot.duckdb")

ticker = "KO"
bot_id = "default"

try:
    print(f"Trying to simulate BUY for {ticker}...")
    existing = db.execute("SELECT ticker, qty, avg_entry_price FROM positions WHERE ticker = ? AND bot_id = ?", [ticker, bot_id]).fetchone()
    print(f"Existing position? {existing}")
    
    # Try inserting assuming not DCA
    try:
        db.execute(
            """
            INSERT OR REPLACE INTO positions
                (ticker, qty, avg_entry_price, opened_at, last_updated, bot_id)
            VALUES (?, ?, ?, current_timestamp, current_timestamp, ?)
            """,
            [ticker, 10, 100.0, bot_id],
        )
        print("Inserted position successfully.")
    except Exception as e:
        print(f"ERROR inserting position: {e}")
        
    try:
        order_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO orders
                (id, ticker, side, qty, price, status, bot_id)
            VALUES (?, ?, 'buy', 10, 100.0, 'filled', ?)
            """,
            [order_id, ticker, bot_id]
        )
        print("Inserted order successfully.")
    except Exception as e:
        print(f"ERROR inserting order: {e}")
        
    db.rollback()
except Exception as e:
    print(f"Fatal test error: {e}")
    
db.close()
