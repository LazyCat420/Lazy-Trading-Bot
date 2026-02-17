"""Quick DB health check — verifies all Phase 8 tables exist and reports row counts."""

from app.database import get_db

TABLES = [
    "price_history", "fundamentals", "financial_history",
    "technicals", "news_articles", "youtube_transcripts",
    "risk_metrics", "balance_sheet", "cash_flows",
    "analyst_data", "insider_activity", "earnings_calendar",
]

def main():
    db = get_db()
    print("=" * 50)
    print("  LAZY TRADING BOT — DB HEALTH CHECK")
    print("=" * 50)
    all_ok = True
    for table in TABLES:
        try:
            count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            status = "OK" if count >= 0 else "EMPTY"
            print(f"  {table:25s}  {status:6s}  {count:>6} rows")
        except Exception as e:
            print(f"  {table:25s}  FAIL    {e}")
            all_ok = False

    print("=" * 50)
    if all_ok:
        print("  All 12 tables exist and are accessible.")
    else:
        print("  SOME TABLES FAILED — see above.")
    print()

if __name__ == "__main__":
    main()
