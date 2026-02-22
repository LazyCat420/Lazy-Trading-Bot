import duckdb

DB_PATH = "data/trading_bot.duckdb"

def migrate():
    print(f"Migrating {DB_PATH}...")
    try:
        con = duckdb.connect(DB_PATH)
        
        # Check if columns exist
        print("Checking quant_scorecards schema...")
        schema = con.execute("DESCRIBE quant_scorecards").fetchall()
        cols = [r[0] for r in schema]
        
        updates = []
        if "trend_template_score" not in cols:
            updates.append("ALTER TABLE quant_scorecards ADD COLUMN trend_template_score DOUBLE DEFAULT 0.0")
        if "vcp_setup_score" not in cols:
            updates.append("ALTER TABLE quant_scorecards ADD COLUMN vcp_setup_score DOUBLE DEFAULT 0.0")
        if "rs_rating" not in cols:
            updates.append("ALTER TABLE quant_scorecards ADD COLUMN rs_rating DOUBLE DEFAULT 0.0")
            
        if updates:
            print(f"Applying {len(updates)} schema updates...")
            for sql in updates:
                print(f"Executing: {sql}")
                con.execute(sql)
            print("Migration successful.")
        else:
            print("Schema is already up to date.")
            
        con.close()
    except Exception as e:
        print(f"Migration failed: {e}")
        # If locked, we can't do anything but warn the user
        if "IO Error" in str(e) or "lock" in str(e).lower():
            print("\n❌ DATABASE IS LOCKED BY THE RUNNING SERVER.")
            print("Please STOP the server (Ctrl+C), run this script, and restart.")

if __name__ == "__main__":
    migrate()
