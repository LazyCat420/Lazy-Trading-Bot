"""Phase 1 pipeline verification - tests quant engine and data distiller."""
import sys
sys.path.insert(0, ".")

print("1. Importing modules...")
from app.engine.quant_signals import QuantSignalEngine
from app.engine.data_distiller import DataDistiller
from app.database import get_db
print("   All imports OK")

print("\n2. Connecting to DB...")
db = get_db()
rows = db.execute("SELECT DISTINCT ticker FROM price_history LIMIT 5").fetchall()
available = [r[0] for r in rows]
print(f"   Tickers with data: {available}")

if not available:
    print("   No tickers. Run data collection first.")
    sys.exit(0)

ticker = available[0]
print(f"   Testing with: {ticker}")

print(f"\n3. Computing quant scorecard for {ticker}...")
try:
    sc = QuantSignalEngine().compute(ticker)
    print(f"   momentum_12m:       {sc.momentum_12m:+.4f}")
    print(f"   mean_reversion:     {sc.mean_reversion_score:+.4f}")
    print(f"   hurst_exponent:     {sc.hurst_exponent:.4f}")
    print(f"   vwap_deviation:     {sc.vwap_deviation:+.4f}")
    print(f"   fama_french_alpha:  {sc.fama_french_alpha:+.4f}")
    print(f"   earnings_yield_gap: {sc.earnings_yield_gap:+.4f}")
    print(f"   altman_z_score:     {sc.altman_z_score:.4f}")
    print(f"   piotroski_f_score:  {sc.piotroski_f_score}/9")
    print(f"   sharpe_ratio:       {sc.sharpe_ratio:.4f}")
    print(f"   kelly_fraction:     {sc.kelly_fraction:.4f}")
    print(f"   flags:              {sc.flags}")
    print("   SCORECARD OK")
except Exception as e:
    print(f"   FAILED: {e}")
    import traceback
    traceback.print_exc()
    sc = None

print(f"\n4. Testing DataDistiller for {ticker}...")
try:
    distiller = DataDistiller()

    prices_raw = db.execute(
        "SELECT * FROM price_history WHERE ticker = ? ORDER BY date", [ticker]
    ).fetchall()
    cols = [desc[0] for desc in db.description]

    class Row:
        def __init__(self, data, columns):
            for c, v in zip(columns, data):
                setattr(self, c, v)

    prices = [Row(r, cols) for r in prices_raw]

    tech_raw = db.execute(
        "SELECT * FROM technicals WHERE ticker = ? ORDER BY date", [ticker]
    ).fetchall()
    tech_cols = [desc[0] for desc in db.description]
    technicals = [Row(r, tech_cols) for r in tech_raw]

    print(f"   Price rows: {len(prices)}, Tech rows: {len(technicals)}")

    pa = distiller.distill_price_action(prices, technicals, sc)
    print(f"\n   PRICE ACTION ({len(pa)} chars, {len(pa.splitlines())} lines):")
    for line in pa.splitlines()[:15]:
        print(f"     {line}")

    fa = distiller.distill_fundamentals(None, None, None, None, sc)
    print(f"\n   FUNDAMENTALS ({len(fa)} chars):")
    for line in fa.splitlines()[:8]:
        print(f"     {line}")

    ra = distiller.distill_risk(None, sc)
    print(f"\n   RISK ({len(ra)} chars):")
    for line in ra.splitlines()[:8]:
        print(f"     {line}")

    print("\n   DISTILLER OK")
except Exception as e:
    print(f"   FAILED: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*50)
print("PHASE 1 VERIFICATION COMPLETE")
print("="*50)
