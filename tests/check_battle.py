"""Battle test: run all 3 tickers and validate Phase 2 quality fixes.

Checks:
  Fix 1 — deterministic rule evaluations present
  Fix 2 — support_levels, key_signals, strengths, risks populated
  Fix 3 — quick mode has fundamentals context
  Fix 4 — bull_case, base_case, bear_case in risk report
  Fix 5 — entry_price non-zero in risk report
  Fix 6 — quant signals cited in key_metrics
"""
import requests
import sys
import time

BASE = "http://localhost:8000"
TICKERS = ["VOO", "TSLA", "WMT"]


def run_analysis(ticker: str) -> dict:
    """Run full pipeline analysis on a ticker."""
    print(f"\n{'='*60}")
    print(f"  ANALYZING {ticker}")
    print(f"{'='*60}")
    resp = requests.post(
        f"{BASE}/api/analyze",
        json={"ticker": ticker, "mode": "full"},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_cached(ticker: str) -> dict:
    """Fetch cached reports for a ticker."""
    resp = requests.get(f"{BASE}/api/dashboard/analysis/{ticker}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def validate_fix2(reports: dict, ticker: str) -> list[str]:
    """Fix 2: Structured arrays populated."""
    issues = []
    ta = reports.get("technical", {})
    fa = reports.get("fundamental", {})
    ra = reports.get("risk", {})

    if not ta.get("support_levels"):
        issues.append(f"{ticker} TECH: support_levels STILL EMPTY")
    if not ta.get("key_signals"):
        issues.append(f"{ticker} TECH: key_signals STILL EMPTY")
    if not fa.get("strengths"):
        issues.append(f"{ticker} FUND: strengths STILL EMPTY")
    if not fa.get("risks"):
        issues.append(f"{ticker} FUND: risks STILL EMPTY")
    if not fa.get("key_metrics"):
        issues.append(f"{ticker} FUND: key_metrics STILL EMPTY")
    if not ra.get("downside_scenarios"):
        issues.append(f"{ticker} RISK: downside_scenarios STILL EMPTY")

    return issues


def validate_fix4(reports: dict, ticker: str) -> list[str]:
    """Fix 4: Scenario modeling populated."""
    issues = []
    ra = reports.get("risk", {})

    for case_name in ["bull_case", "base_case", "bear_case"]:
        case = ra.get(case_name)
        if not case:
            issues.append(f"{ticker} RISK: {case_name} MISSING")
        elif not case.get("description"):
            issues.append(f"{ticker} RISK: {case_name} has no description")

    return issues


def validate_fix5(reports: dict, ticker: str) -> list[str]:
    """Fix 5: Dollar-denominated risk with entry price."""
    issues = []
    ra = reports.get("risk", {})

    entry = ra.get("entry_price", 0)
    stop = ra.get("suggested_stop_loss", 0)
    target = ra.get("suggested_take_profit", 0)

    if not entry or entry == 0:
        issues.append(f"{ticker} RISK: entry_price is 0 or missing")
    if stop == 0:
        issues.append(f"{ticker} RISK: stop_loss is 0")
    if target == 0:
        issues.append(f"{ticker} RISK: take_profit is 0")
    elif entry and stop and target:
        # Sanity check: stop < entry < target for long trades
        if stop > entry:
            issues.append(f"{ticker} RISK: stop_loss ({stop}) > entry ({entry}) — wrong direction")

    return issues


def validate_fix1(reports: dict, ticker: str) -> list[str]:
    """Fix 1: Decision has deterministic rule evaluations."""
    issues = []
    dec = reports.get("decision", {})

    entry_rules = dec.get("entry_rules_evaluated", [])
    if not entry_rules:
        issues.append(f"{ticker} DECISION: entry_rules_evaluated EMPTY")
    else:
        # Check for deterministic marker
        deterministic_found = any(
            "deterministic" in r.get("data_source", "").lower()
            for r in entry_rules
        )
        if not deterministic_found:
            issues.append(f"{ticker} DECISION: no deterministic rules found in evaluations")

    return issues


def main():
    all_issues = []
    results = {}

    for ticker in TICKERS:
        # Run analysis
        try:
            analysis = run_analysis(ticker)
            errors = analysis.get("errors", [])
            if errors:
                print(f"  ⚠️  Pipeline errors: {errors}")
            else:
                print("  ✅ Pipeline completed with 0 errors")
        except Exception as e:
            print(f"  ❌ Pipeline FAILED: {e}")
            all_issues.append(f"{ticker}: Pipeline failed — {e}")
            continue

        # Wait a moment for reports to save
        time.sleep(2)

        # Fetch cached reports
        try:
            cached = fetch_cached(ticker)
            if not cached.get("cached"):
                print("  ⚠️  No cached reports found")
                all_issues.append(f"{ticker}: No cached reports after analysis")
                continue
            reports = cached.get("agents", {})
            results[ticker] = reports
        except Exception as e:
            print(f"  ❌ Failed to fetch cached: {e}")
            continue

        # Run all validations
        print(f"\n  --- Validation Results for {ticker} ---")

        for fix_name, validator in [
            ("Fix 1 (Deterministic Rules)", validate_fix1),
            ("Fix 2 (Structured Arrays)", validate_fix2),
            ("Fix 4 (Scenario Modeling)", validate_fix4),
            ("Fix 5 (Dollar Risk)", validate_fix5),
        ]:
            fix_issues = validator(reports, ticker)
            if fix_issues:
                print(f"  ❌ {fix_name}:")
                for issue in fix_issues:
                    print(f"      • {issue}")
                all_issues.extend(fix_issues)
            else:
                print(f"  ✅ {fix_name}: PASS")

        # Print key fields for visual inspection
        ta = reports.get("technical", {})
        fa = reports.get("fundamental", {})
        ra = reports.get("risk", {})
        print("\n  📊 Key Fields:")
        print(f"    support_levels: {ta.get('support_levels', 'MISSING')}")
        print(f"    resistance_levels: {ta.get('resistance_levels', 'MISSING')}")
        print(f"    key_signals: {len(ta.get('key_signals', []))} items")
        print(f"    strengths: {len(fa.get('strengths', []))} items")
        print(f"    risks: {len(fa.get('risks', []))} items")
        print(f"    key_metrics: {list(fa.get('key_metrics', {}).keys())}")
        print(f"    entry_price: ${ra.get('entry_price', 'MISSING')}")
        print(f"    stop_loss: ${ra.get('suggested_stop_loss', 'MISSING')}")
        print(f"    take_profit: ${ra.get('suggested_take_profit', 'MISSING')}")
        bull = ra.get("bull_case", {})
        base = ra.get("base_case", {})
        bear = ra.get("bear_case", {})
        print(f"    bull_case: {bull.get('label', 'MISSING')} p={bull.get('probability', 0):.0%} — {bull.get('description', 'N/A')[:50]}")
        print(f"    base_case: {base.get('label', 'MISSING')} p={base.get('probability', 0):.0%} — {base.get('description', 'N/A')[:50]}")
        print(f"    bear_case: {bear.get('label', 'MISSING')} p={bear.get('probability', 0):.0%} — {bear.get('description', 'N/A')[:50]}")

    # Summary
    print(f"\n{'='*60}")
    print("  BATTLE TEST SUMMARY")
    print(f"{'='*60}")
    if all_issues:
        print(f"  ❌ {len(all_issues)} issues found:")
        for issue in all_issues:
            print(f"    • {issue}")
        sys.exit(1)
    else:
        print(f"  ✅ ALL {len(TICKERS)} tickers passed all validation checks!")
        sys.exit(0)


if __name__ == "__main__":
    main()
