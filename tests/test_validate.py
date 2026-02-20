"""Quick validation — check cached reports for all 3 tickers."""
import json
import requests

BASE = "http://localhost:8000"

for ticker in ["VOO", "TSLA", "WMT"]:
    print(f"\n{'='*50}")
    print(f"  {ticker}")
    print(f"{'='*50}")
    r = requests.get(f"{BASE}/api/dashboard/analysis/{ticker}", timeout=30)
    d = r.json()

    if not d.get("cached"):
        print("  ⚠️  No cached reports")
        continue

    a = d.get("agents", {})
    ta = a.get("technical", {})
    fa = a.get("fundamental", {})
    ra = a.get("risk", {})
    dec = a.get("decision", {})

    # Fix 2 checks
    sl = ta.get("support_levels", [])
    rl = ta.get("resistance_levels", [])
    ks = ta.get("key_signals", [])
    st = fa.get("strengths", [])
    rk = fa.get("risks", [])
    km = fa.get("key_metrics", {})
    ds = ra.get("downside_scenarios", [])

    print(f"  Fix 2:")
    print(f"    support_levels: {len(sl)} -> {sl[:3]}")
    print(f"    resistance_levels: {len(rl)} -> {rl[:3]}")
    print(f"    key_signals: {len(ks)} items")
    for s in ks[:3]:
        print(f"      • {s[:80]}")
    print(f"    strengths: {len(st)} items")
    print(f"    risks: {len(rk)} items")
    print(f"    key_metrics: {list(km.keys())[:5]}")
    print(f"    downside_scenarios: {len(ds)} items")

    # Fix 4 checks
    print(f"  Fix 4:")
    for cn in ["bull_case", "base_case", "bear_case"]:
        c = ra.get(cn)
        if c:
            print(f"    {cn}: {c.get('label','')} p={c.get('probability',0):.0%} — {c.get('description','')[:60]}")
        else:
            print(f"    {cn}: MISSING")

    # Fix 5 checks
    print(f"  Fix 5:")
    print(f"    entry_price: ${ra.get('entry_price', 0):,.2f}")
    print(f"    stop_loss: ${ra.get('suggested_stop_loss', 0):,.2f}")
    print(f"    take_profit: ${ra.get('suggested_take_profit', 0):,.2f}")

    # Fix 1 checks
    print(f"  Fix 1:")
    er = dec.get("entry_rules_evaluated", [])
    print(f"    {len(er)} entry rules evaluated:")
    for rule in er[:5]:
        met = "MET" if rule.get("is_met") else "NOT MET"
        src = rule.get("data_source", "")
        txt = rule.get("rule_text", "")[:60]
        print(f"      [{met}] {txt} ({src[:30]})")
