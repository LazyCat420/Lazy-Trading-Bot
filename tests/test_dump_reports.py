"""Dump full agent reports for quality review against institutional standards."""
import json
import requests

BASE = "http://localhost:8000"
TICKERS = ["VOO", "TSLA", "WMT"]

all_reports = {}

for ticker in TICKERS:
    r = requests.get(f"{BASE}/api/dashboard/analysis/{ticker}", timeout=30)
    data = r.json()
    all_reports[ticker] = data

# Write full output to file for review
with open("tests/quality_report.json", "w", encoding="utf-8") as f:
    json.dump(all_reports, f, indent=2, default=str)

# Print summary
for ticker, data in all_reports.items():
    print(f"\n{'='*70}")
    print(f"  {ticker}")
    print(f"{'='*70}")
    
    if not data.get("cached"):
        print("  No cached reports")
        continue
    
    agents = data.get("agents", {})
    for name, report in agents.items():
        print(f"\n  [{name.upper()}]")
        if not report:
            print("    (empty)")
            continue
        
        # Key quantitative fields
        for key in ["signal", "confidence", "risk_grade", "sentiment_score",
                     "risk_reward_ratio", "stop_loss", "take_profit",
                     "position_size_pct", "max_loss_pct"]:
            if key in report:
                print(f"    {key}: {report[key]}")
        
        # Key qualitative fields
        if "key_signals" in report:
            signals = report["key_signals"]
            print(f"    key_signals ({len(signals)}):")
            for s in signals[:5]:
                print(f"      - {s[:120]}")
        
        if "rationale" in report:
            rat = report["rationale"]
            # Show first 300 chars
            print(f"    rationale preview:")
            print(f"      {rat[:400]}...")

    # Decision
    decision = data.get("agents", {}).get("decision", {})
    if decision:
        print(f"\n  [FINAL DECISION]")
        for key in ["action", "confidence", "conviction_score"]:
            if key in decision:
                print(f"    {key}: {decision[key]}")
        if "reasoning" in decision:
            print(f"    reasoning: {decision['reasoning'][:300]}...")

print(f"\nFull reports saved to tests/quality_report.json")
