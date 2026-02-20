"""Run full pipeline on diverse tickers and dump agent reports for quality review."""
import sys
import json
import requests

BASE = "http://localhost:8000"
TICKERS = ["VOO", "TSLA", "WMT"]

for ticker in TICKERS:
    print(f"\n{'='*70}")
    print(f"ANALYZING: {ticker}")
    print(f"{'='*70}")

    try:
        r = requests.post(
            f"{BASE}/api/analyze",
            json={"ticker": ticker, "mode": "quick"},
            timeout=300,
        )
        data = r.json()
    except Exception as e:
        print(f"  REQUEST FAILED: {e}")
        continue

    errors = data.get("errors", [])
    print(f"  Status: {r.status_code}")
    print(f"  Errors: {errors}")

    # Show quant scorecard status
    qs = data.get("pipeline_status", {}).get("quant_scorecard", {})
    print(f"  Quant Scorecard: {qs}")

    # Show agent reports
    agents = data.get("agent_reports", {})
    for agent_name, report in agents.items():
        print(f"\n  --- {agent_name.upper()} AGENT REPORT ---")
        if isinstance(report, dict):
            # Print key fields
            for key in ["signal", "confidence", "risk_grade",
                         "sentiment_score", "key_signals", "rationale",
                         "stop_loss", "take_profit", "position_size_pct",
                         "risk_reward_ratio"]:
                if key in report:
                    val = report[key]
                    if isinstance(val, str) and len(val) > 200:
                        val = val[:200] + "..."
                    elif isinstance(val, list):
                        val = val[:5]
                    print(f"    {key}: {val}")
        else:
            text = str(report)
            print(f"    {text[:300]}")

print(f"\n{'='*70}")
print("QUALITY AUDIT COMPLETE")
print(f"{'='*70}")
