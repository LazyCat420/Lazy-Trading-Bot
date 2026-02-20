"""Minimal import check."""
import sys
sys.path.insert(0, ".")

print("1. Importing database...")
try:
    from app.database import get_db, init_db
    print("   SUCCESS")
except Exception as e:
    print(f"   FAILED: {type(e).__name__}: {e}")
    sys.exit(1)

print("2. Calling init_db()...")
try:
    init_db()
    print("   SUCCESS")
except Exception as e:
    print(f"   FAILED: {type(e).__name__}: {e}")
    sys.exit(1)

print("3. Checking tickers...")
db = get_db()
rows = db.execute("SELECT DISTINCT ticker FROM price_history LIMIT 5").fetchall()
print(f"   Tickers: {[r[0] for r in rows]}")
print("ALL OK")
