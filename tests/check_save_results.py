"""Save validation results to JSON — uses correct nested structure."""
import json
import requests

BASE = "http://localhost:8000"
results = {}

for ticker in ["VOO", "TSLA", "WMT"]:
    r = requests.get(f"{BASE}/api/dashboard/analysis/{ticker}", timeout=30)
    d = r.json()
    if not d.get("cached"):
        results[ticker] = {"error": "no cached reports"}
        continue

    agents = d.get("agents", {})
    # Dashboard wraps each agent as {"status": "ok", "report": {...}}
    ta = agents.get("technical", {}).get("report", {})
    fa = agents.get("fundamental", {}).get("report", {})
    ra = agents.get("risk", {}).get("report", {})
    dec = d.get("decision", {})

    results[ticker] = {
        "fix2_support_levels": ta.get("support_levels", []),
        "fix2_resistance_levels": ta.get("resistance_levels", []),
        "fix2_key_signals": ta.get("key_signals", []),
        "fix2_strengths": fa.get("strengths", []),
        "fix2_risks": fa.get("risks", []),
        "fix2_key_metrics": fa.get("key_metrics", {}),
        "fix2_downside_scenarios": ra.get("downside_scenarios", []),
        "fix4_bull_case": ra.get("bull_case"),
        "fix4_base_case": ra.get("base_case"),
        "fix4_bear_case": ra.get("bear_case"),
        "fix5_entry_price": ra.get("entry_price", 0),
        "fix5_stop_loss": ra.get("suggested_stop_loss", 0),
        "fix5_take_profit": ra.get("suggested_take_profit", 0),
        "fix1_entry_rules": dec.get("entry_rules_evaluated", []),
        "fix1_signal": dec.get("signal"),
    }

with open("tests/validation_results.json", "w") as f:
    json.dump(results, f, indent=2, default=str)

# Print summary
for t, r in results.items():
    if "error" in r:
        print(f"{t}: {r['error']}")
        continue
    f2 = sum([
        len(r["fix2_support_levels"]) > 0,
        len(r["fix2_key_signals"]) > 0,
        len(r["fix2_strengths"]) > 0,
        len(r["fix2_risks"]) > 0,
        len(r["fix2_key_metrics"]) > 0,
    ])
    f4 = sum([r["fix4_bull_case"] is not None, r["fix4_base_case"] is not None, r["fix4_bear_case"] is not None])
    f5 = r["fix5_entry_price"] > 0
    f1 = len(r["fix1_entry_rules"]) > 0
    det = sum(1 for x in r["fix1_entry_rules"] if "deterministic" in (x.get("data_source") or "").lower())
    print(f"{t}: Fix2={f2}/5 Fix4={f4}/3 Fix5={'Y' if f5 else 'N'} Fix1={len(r['fix1_entry_rules'])}rules({det}det)")
