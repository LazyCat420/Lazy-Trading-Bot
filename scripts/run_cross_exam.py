"""Cross-Examination & A/B Benchmark Suite for Brain Loop.

Tests:
  1. Hallucination Injection — feed fake data, verify LLM doesn't invent more
  2. Contradiction Forcing — inject conflicting signals, verify detection
  3. Domain Ablation — remove domains one-at-a-time, measure decision drift
  4. Signal Consistency — thesis direction must match final decision
  5. Citation Round-Trip — every cited value must exist in input
  6. A/B: Brain Loop vs Legacy — speed + accuracy comparison

Usage:
    python scripts/run_cross_exam.py
    python scripts/run_cross_exam.py --test hallucination
    python scripts/run_cross_exam.py --test ab
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DB_PROFILE", "test")

# ── Output formatting ────────────────────────────────────────────
_W = 72
def banner(title: str) -> None:
    print(f"\n{'═' * _W}")
    print(f"  CROSS-EXAM: {title}")
    print(f"{'═' * _W}\n")

def section(label: str) -> None:
    print(f"\n  ── {label} ──")

def ok(msg: str) -> None:
    print(f"  ✅ {msg}")

def warn(msg: str) -> None:
    print(f"  ⚠️  {msg}")

def fail(msg: str) -> None:
    print(f"  ❌ {msg}")

def data(label: str, value: str) -> None:
    print(f"  {label}: {value}")


# ══════════════════════════════════════════════════════════════════
# Test 1: Hallucination Injection
# ══════════════════════════════════════════════════════════════════

async def test_hallucination_injection() -> dict:
    """Feed obviously fake data and check if LLM parrots or invents more.

    We inject a synthetic technical dataset with known values:
      RSI=99.99, MACD=+999.0, close=$1.00
    Then verify:
      - LLM cites these exact (real) values
      - LLM does NOT cite values not in the data
      - Signal is extreme (RSI=99 should trigger overbought detection)
    """
    banner("Test 1: Hallucination Injection")

    from app.services.brain_loop import AnalystAgent, validate_memo_citations

    # Synthetic data with obviously fake but specific values
    fake_technical = (
        "Symbol: FAKETEST\n"
        "Current Price: $1.00\n"
        "Today Change: +99.99%\n"
        "Volume: 1 (Avg: 1,000,000)\n\n"
        "Last 10 trading days (newest first):\n"
        "date=2026-03-16 | close=1.0000 | rsi=99.9900 | macd=999.0000 | "
        "macd_signal=500.0000 | macd_hist=499.0000 | "
        "sma_20=0.5000 | sma_50=0.2500 | sma_200=0.1000 | "
        "atr=0.0100 | stoch_k=99.0000 | stoch_d=98.0000 | adx=80.0000"
    )

    domain_data = {"technical": fake_technical}

    t0 = time.perf_counter()
    memo = await AnalystAgent.analyze_domain("technical", fake_technical, "FAKETEST")
    elapsed = time.perf_counter() - t0

    section("Memo Result")
    data("Signal", memo.get("signal", "?"))
    data("Confidence", f"{memo.get('confidence', 0):.2f}")
    data("Finding", memo.get("key_finding", "?")[:120])
    data("Time", f"{elapsed:.1f}s")

    # Validate citations
    citation_check = validate_memo_citations([memo], domain_data)
    cr = citation_check[0] if citation_check else {}

    section("Citation Accuracy")
    data("Total cited", str(cr.get("total_cited", 0)))
    data("Verified", str(cr.get("verified", 0)))
    data("Hallucinated", str(cr.get("hallucinated", 0)))
    data("Score", f"{cr.get('score', 0):.0%}")

    for d in cr.get("details", []):
        status = "✅" if "verified" in d.get("status", "") else "⚠️ HALLUCINATED"
        print(f"    {status} {d['cite']}")

    # Validation
    results = {"test": "hallucination_injection", "passed": True, "checks": []}

    # Check 1: Signal should reflect extreme overbought
    signal = memo.get("signal", "NEUTRAL")
    if signal in ["BEARISH", "NEUTRAL"]:
        ok(f"Signal={signal} — correctly detected RSI=99 as overbought")
        results["checks"].append({"check": "overbought_detection", "pass": True})
    elif signal == "BULLISH":
        warn(f"Signal=BULLISH despite RSI=99 — model missed overbought")
        results["checks"].append({"check": "overbought_detection", "pass": False})
        results["passed"] = False

    # Check 2: Citation accuracy
    score = cr.get("score", 0)
    if score >= 0.8:
        ok(f"Citation accuracy: {score:.0%} — LLM used real data")
        results["checks"].append({"check": "citation_accuracy", "pass": True})
    else:
        fail(f"Citation accuracy: {score:.0%} — too many hallucinations")
        results["checks"].append({"check": "citation_accuracy", "pass": False})
        results["passed"] = False

    # Check 3: No invented values (check for common hallucination patterns)
    key_finding = memo.get("key_finding", "").lower()
    halluc_phrases = ["earnings", "revenue", "pe ratio", "dividend", "forward pe"]
    invented = [p for p in halluc_phrases if p in key_finding]
    if invented:
        fail(f"LLM invented data not in input: {', '.join(invented)}")
        results["checks"].append({"check": "no_invented_data", "pass": False})
        results["passed"] = False
    else:
        ok("No invented data detected in key_finding")
        results["checks"].append({"check": "no_invented_data", "pass": True})

    results["elapsed_s"] = round(elapsed, 2)
    results["memo"] = memo
    return results


# ══════════════════════════════════════════════════════════════════
# Test 2: Contradiction Forcing
# ══════════════════════════════════════════════════════════════════

async def test_contradiction_forcing() -> dict:
    """Inject memos with deliberately opposing signals and verify detection."""
    banner("Test 2: Contradiction Forcing")

    from app.services.brain_loop import ThesisConstructor

    # Craft memos with extreme contradictions
    contradictory_memos = [
        {
            "domain": "technical",
            "label": "Technical Analysis",
            "signal": "BULLISH",
            "confidence": 0.95,
            "key_finding": "RSI=25 (oversold), MACD golden cross, price above SMA200",
            "data_cited": ["RSI=25", "MACD_cross=golden", "SMA200=below_price"],
            "risks": ["Overbought on shorter timeframes"],
            "recommendation_weight": 0.8,
            "elapsed_s": 0,
        },
        {
            "domain": "fundamental",
            "label": "Fundamental Analysis",
            "signal": "BEARISH",
            "confidence": 0.90,
            "key_finding": "P/E=85, debt/equity=3.5, negative FCF of -$500M",
            "data_cited": ["P/E=85", "debt_equity=3.5", "FCF=-$500M"],
            "risks": ["Extreme overvaluation", "Balance sheet distress"],
            "recommendation_weight": 0.9,
            "elapsed_s": 0,
        },
        {
            "domain": "sentiment",
            "label": "Sentiment & News",
            "signal": "BULLISH",
            "confidence": 0.85,
            "key_finding": "Massive positive momentum on Reddit, 95% bullish mentions",
            "data_cited": ["reddit_bullish=95%", "mention_count=5000"],
            "risks": ["Echo chamber risk"],
            "recommendation_weight": 0.5,
            "elapsed_s": 0,
        },
        {
            "domain": "smart_money",
            "label": "Smart Money",
            "signal": "BEARISH",
            "confidence": 0.88,
            "key_finding": "CEO sold 90% of holdings ($50M), no institutional buying",
            "data_cited": ["CEO_sold=90%", "insider_selling=$50M", "institutional_buying=0"],
            "risks": ["Massive insider selling is a red flag"],
            "recommendation_weight": 0.9,
            "elapsed_s": 0,
        },
        {
            "domain": "risk",
            "label": "Risk Assessment",
            "signal": "BEARISH",
            "confidence": 0.92,
            "key_finding": "Altman Z=0.8 (distress zone), earnings in 2 days, max drawdown=-45%",
            "data_cited": ["altman_z=0.8", "days_until_earnings=2", "max_drawdown=-45%"],
            "risks": ["Bankruptcy risk", "Imminent earnings volatility"],
            "recommendation_weight": 0.95,
            "elapsed_s": 0,
        },
    ]

    portfolio_text = "Cash: $100,000\nTotal Value: $100,000\nPositions: 0"

    t0 = time.perf_counter()
    thesis = await ThesisConstructor.synthesize(
        contradictory_memos, portfolio_text, "FAKETEST",
    )
    elapsed = time.perf_counter() - t0

    section("Thesis Result")
    data("Direction", thesis.get("direction", "?"))
    data("Confidence", f"{thesis.get('weighted_confidence', 0):.2f}")
    data("Action", thesis.get("recommended_action", "?"))
    data("Contradictions found", str(len(thesis.get("contradictions", []))))
    data("Time", f"{elapsed:.1f}s")

    for c in thesis.get("contradictions", []):
        print(f"    ⚠️  {c}")

    results = {"test": "contradiction_forcing", "passed": True, "checks": []}

    # Check 1: Must detect at least 2 contradictions (we planted BULL/tech + BEAR/fundamental + BEAR/smart)
    n_contra = len(thesis.get("contradictions", []))
    if n_contra >= 2:
        ok(f"Detected {n_contra} contradictions (expected ≥2)")
        results["checks"].append({"check": "contradiction_count", "pass": True})
    else:
        fail(f"Only {n_contra} contradictions detected (expected ≥2)")
        results["checks"].append({"check": "contradiction_count", "pass": False})
        results["passed"] = False

    # Check 2: Direction should NOT be BULLISH (3/5 memos are BEARISH)
    direction = thesis.get("direction", "")
    if direction != "BULLISH":
        ok(f"Direction={direction} — correctly weighted BEARISH majority (3/5)")
        results["checks"].append({"check": "direction_weighted", "pass": True})
    else:
        fail("Direction=BULLISH despite 3/5 BEARISH memos — weighting broken")
        results["checks"].append({"check": "direction_weighted", "pass": False})
        results["passed"] = False

    # Check 3: Action should be HOLD or SELL (not BUY)
    action = thesis.get("recommended_action", "")
    if action in ["HOLD", "SELL"]:
        ok(f"Action={action} — correct given BEARISH majority + high risk")
        results["checks"].append({"check": "action_correct", "pass": True})
    else:
        fail(f"Action={action} — should be HOLD or SELL")
        results["checks"].append({"check": "action_correct", "pass": False})
        results["passed"] = False

    results["elapsed_s"] = round(elapsed, 2)
    results["thesis"] = thesis
    return results


# ══════════════════════════════════════════════════════════════════
# Test 3: Domain Ablation
# ══════════════════════════════════════════════════════════════════

async def test_domain_ablation() -> dict:
    """Remove one domain at a time and check decision stability."""
    banner("Test 3: Domain Ablation")

    from app.services.brain_loop import AnalystAgent, ThesisConstructor, extract_domain_data

    # Get real data
    from app.database import get_db
    db = get_db()

    # Build a minimal context
    context = {"last_price": 211.75, "today_change_pct": 0.5, "volume": 50_000_000, "avg_volume": 60_000_000}
    domain_data = extract_domain_data(context, "AAPL")

    all_domains = list(domain_data.keys())
    section(f"Available domains: {', '.join(all_domains)}")

    # Run full (baseline)
    section("Baseline: ALL domains")
    t0 = time.perf_counter()
    full_memos = await AnalystAgent.run_all_domains(domain_data, "AAPL")
    full_thesis = await ThesisConstructor.synthesize(
        full_memos, "Cash: $100,000\nTotal Value: $100,000\nPositions: 0", "AAPL",
    )
    baseline_elapsed = time.perf_counter() - t0
    baseline_direction = full_thesis.get("direction", "?")
    baseline_conf = full_thesis.get("weighted_confidence", 0)
    baseline_action = full_thesis.get("recommended_action", "?")

    data("Baseline", f"{baseline_direction} ({baseline_action}) conf={baseline_conf:.2f} [{baseline_elapsed:.1f}s]")

    # Now remove one domain at a time
    ablation_results = []
    for drop_domain in all_domains:
        section(f"Ablation: WITHOUT {drop_domain}")
        reduced_data = {k: v for k, v in domain_data.items() if k != drop_domain}
        reduced_memos = await AnalystAgent.run_all_domains(reduced_data, "AAPL")
        reduced_thesis = await ThesisConstructor.synthesize(
            reduced_memos, "Cash: $100,000\nTotal Value: $100,000\nPositions: 0", "AAPL",
        )

        direction = reduced_thesis.get("direction", "?")
        conf = reduced_thesis.get("weighted_confidence", 0)
        action = reduced_thesis.get("recommended_action", "?")
        conf_drift = abs(conf - baseline_conf)

        result = {
            "dropped": drop_domain,
            "direction": direction,
            "action": action,
            "confidence": conf,
            "conf_drift": round(conf_drift, 3),
            "direction_changed": direction != baseline_direction,
            "action_changed": action != baseline_action,
        }
        ablation_results.append(result)

        drift_icon = "🔴" if conf_drift > 0.15 else "🟡" if conf_drift > 0.05 else "🟢"
        data(
            f"  ─{drop_domain}",
            f"{direction} ({action}) conf={conf:.2f} drift={conf_drift:+.3f} {drift_icon}",
        )

    results = {"test": "domain_ablation", "passed": True, "checks": []}

    # Check: No single domain removal should flip the action
    flips = [r for r in ablation_results if r["action_changed"]]
    if not flips:
        ok("No domain ablation changed the final action — decision is robust")
        results["checks"].append({"check": "action_stability", "pass": True})
    else:
        for f in flips:
            warn(f"Removing {f['dropped']} changed action to {f['action']}!")
        results["checks"].append({"check": "action_stability", "pass": False, "flips": [f["dropped"] for f in flips]})
        # Not a hard fail — informational

    # Check: Max confidence drift < 0.20
    max_drift = max(r["conf_drift"] for r in ablation_results)
    if max_drift < 0.20:
        ok(f"Max confidence drift: {max_drift:.3f} (<0.20 threshold)")
        results["checks"].append({"check": "confidence_stability", "pass": True})
    else:
        warn(f"Max confidence drift: {max_drift:.3f} (>0.20 threshold)")
        results["checks"].append({"check": "confidence_stability", "pass": False})

    results["baseline"] = {
        "direction": baseline_direction, "action": baseline_action,
        "confidence": baseline_conf, "elapsed_s": round(baseline_elapsed, 2),
    }
    results["ablations"] = ablation_results
    return results


# ══════════════════════════════════════════════════════════════════
# Test 4: Signal Consistency
# ══════════════════════════════════════════════════════════════════

async def test_signal_consistency() -> dict:
    """Verify thesis direction is consistent with final decision."""
    banner("Test 4: Signal Consistency")

    from app.services.brain_loop import (
        AnalystAgent, DecisionAgent, ThesisConstructor, extract_domain_data,
    )

    context = {"last_price": 211.75, "today_change_pct": 0.5, "volume": 50_000_000, "avg_volume": 60_000_000}
    domain_data = extract_domain_data(context, "AAPL")
    portfolio = {"cash": 100_000, "total_value": 100_000, "positions": []}

    t0 = time.perf_counter()
    memos = await AnalystAgent.run_all_domains(domain_data, "AAPL")
    thesis = await ThesisConstructor.synthesize(
        memos, "Cash: $100,000\nTotal Value: $100,000\nPositions: 0", "AAPL",
    )
    _, decision = await DecisionAgent.decide(thesis, "AAPL", portfolio)
    elapsed = time.perf_counter() - t0

    section("Results")
    data("Thesis direction", thesis.get("direction", "?"))
    data("Thesis recommendation", thesis.get("recommended_action", "?"))
    data("Final decision", decision.get("action", "?"))
    data("Time", f"{elapsed:.1f}s")

    results = {"test": "signal_consistency", "passed": True, "checks": []}

    thesis_dir = thesis.get("direction", "")
    thesis_rec = thesis.get("recommended_action", "")
    final_act = decision.get("action", "")

    # Check 1: Direction → Decision consistency
    consistency_map = {
        "BULLISH": ["BUY", "HOLD"],    # BULLISH can → BUY or HOLD (if cash-limited)
        "BEARISH": ["SELL", "HOLD"],    # BEARISH can → SELL or HOLD (if no position)
        "NEUTRAL": ["HOLD"],            # NEUTRAL → HOLD
    }
    valid_actions = consistency_map.get(thesis_dir, ["HOLD"])
    if final_act in valid_actions:
        ok(f"Decision {final_act} is consistent with thesis {thesis_dir}")
        results["checks"].append({"check": "direction_consistency", "pass": True})
    else:
        fail(f"Decision {final_act} CONTRADICTS thesis {thesis_dir} (expected {valid_actions})")
        results["checks"].append({"check": "direction_consistency", "pass": False})
        results["passed"] = False

    # Check 2: Thesis recommendation → Decision match
    if thesis_rec == final_act:
        ok(f"Decision matches thesis recommendation: {thesis_rec}")
        results["checks"].append({"check": "recommendation_match", "pass": True})
    else:
        warn(f"Decision={final_act} differs from recommendation={thesis_rec}")
        results["checks"].append({"check": "recommendation_match", "pass": False})

    # Check 3: Confidence consistency (±0.15)
    thesis_conf = thesis.get("weighted_confidence", 0)
    decision_conf = decision.get("confidence", 0)
    conf_diff = abs(thesis_conf - decision_conf)
    if conf_diff <= 0.15:
        ok(f"Confidence consistent: thesis={thesis_conf:.2f}, decision={decision_conf:.2f} (diff={conf_diff:.2f})")
        results["checks"].append({"check": "confidence_consistent", "pass": True})
    else:
        warn(f"Confidence drift: thesis={thesis_conf:.2f}, decision={decision_conf:.2f} (diff={conf_diff:.2f})")
        results["checks"].append({"check": "confidence_consistent", "pass": False})

    results["elapsed_s"] = round(elapsed, 2)
    return results


# ══════════════════════════════════════════════════════════════════
# Test 5: Citation Round-Trip
# ══════════════════════════════════════════════════════════════════

async def test_citation_roundtrip() -> dict:
    """Full citation accuracy check across all domains."""
    banner("Test 5: Citation Round-Trip")

    from app.services.brain_loop import (
        AnalystAgent, extract_domain_data, validate_memo_citations,
    )

    context = {"last_price": 211.75, "today_change_pct": 0.5, "volume": 50_000_000, "avg_volume": 60_000_000}
    domain_data = extract_domain_data(context, "AAPL")

    t0 = time.perf_counter()
    memos = await AnalystAgent.run_all_domains(domain_data, "AAPL")
    elapsed = time.perf_counter() - t0

    citation_results = validate_memo_citations(memos, domain_data)

    section("Citation Accuracy Per Domain")
    total_cited = 0
    total_verified = 0
    total_halluc = 0

    for cr in citation_results:
        total_cited += cr["total_cited"]
        total_verified += cr["verified"]
        total_halluc += cr["hallucinated"]

        icon = "✅" if cr["score"] >= 0.8 else "⚠️" if cr["score"] >= 0.5 else "❌"
        data(
            f"  {icon} {cr['domain']}",
            f"{cr['verified']}/{cr['total_cited']} verified ({cr['score']:.0%})",
        )
        for d in cr.get("details", []):
            if "not found" in d.get("status", ""):
                print(f"      ⚠️  HALLUCINATED: {d['cite']}")

    overall_score = total_verified / total_cited if total_cited > 0 else 0

    section("Summary")
    data("Total citations", str(total_cited))
    data("Verified", str(total_verified))
    data("Hallucinated", str(total_halluc))
    data("Overall accuracy", f"{overall_score:.0%}")
    data("Time", f"{elapsed:.1f}s")

    results = {"test": "citation_roundtrip", "passed": True, "checks": []}

    if overall_score >= 0.75:
        ok(f"Overall citation accuracy {overall_score:.0%} ≥ 75% threshold")
        results["checks"].append({"check": "citation_threshold", "pass": True})
    else:
        fail(f"Overall citation accuracy {overall_score:.0%} < 75% threshold")
        results["checks"].append({"check": "citation_threshold", "pass": False})
        results["passed"] = False

    results["elapsed_s"] = round(elapsed, 2)
    results["overall_accuracy"] = round(overall_score, 3)
    results["per_domain"] = citation_results
    return results


# ══════════════════════════════════════════════════════════════════
# Test 6: A/B Brain Loop vs Legacy
# ══════════════════════════════════════════════════════════════════

async def test_ab_comparison() -> dict:
    """Run brain loop vs legacy on same data, compare speed and quality."""
    banner("Test 6: A/B — Brain Loop vs Legacy")

    from app.models.trade_action import TradeAction
    from app.services.trading_agent import TradingAgent
    from app.services.trading_pipeline_service import TradingPipelineService

    agent = TradingAgent()

    # Build context manually (same as pipeline would)
    from app.database import get_db
    db = get_db()

    # Get price data
    price_row = db.execute(
        "SELECT close, date FROM price_history WHERE ticker='AAPL' ORDER BY date DESC LIMIT 1"
    ).fetchone()
    last_price = price_row[0] if price_row else 211.75

    # Build a context dict matching what the pipeline builds
    context = {
        "symbol": "AAPL",
        "last_price": last_price,
        "today_change_pct": 0.5,
        "volume": 50_000_000,
        "avg_volume": 60_000_000,
        "portfolio_cash": 100_000,
        "portfolio_value": 100_000,
        "all_positions": [],
        "rag_context": "",
        "quant_flags": [],
    }

    # ── Run A: Brain Loop (new) ───────────────────────────────
    section("A: Brain Loop (3-phase)")
    t0a = time.perf_counter()
    action_a, raw_a, meta_a = await agent.decide(context, bot_id="bench_A")
    elapsed_a = time.perf_counter() - t0a

    data("  Decision", f"{action_a.action} {action_a.symbol}")
    data("  Confidence", f"{action_a.confidence:.2f}")
    data("  Time", f"{elapsed_a:.1f}s")
    data("  LLM calls", str(meta_a.get("turns", "?")))
    data("  Rationale length", f"{len(action_a.rationale)} chars")

    # ── Run B: Legacy (multi-turn) ────────────────────────────
    section("B: Legacy (multi-turn)")
    t0b = time.perf_counter()
    action_b, raw_b, meta_b = await agent._decide_legacy(context, bot_id="bench_B")
    elapsed_b = time.perf_counter() - t0b

    data("  Decision", f"{action_b.action} {action_b.symbol}")
    data("  Confidence", f"{action_b.confidence:.2f}")
    data("  Time", f"{elapsed_b:.1f}s")
    data("  LLM calls", str(meta_b.get("turns", "?")))
    data("  Rationale length", f"{len(action_b.rationale)} chars")

    # ── Comparison ────────────────────────────────────────────
    section("Comparison")
    speed_diff = elapsed_a - elapsed_b
    data("Speed diff", f"{speed_diff:+.1f}s ({'Brain Loop faster' if speed_diff < 0 else 'Legacy faster'})")
    data("Action match", "✅ Yes" if action_a.action == action_b.action else f"❌ No ({action_a.action} vs {action_b.action})")
    data("Confidence diff", f"{abs(action_a.confidence - action_b.confidence):.2f}")

    # Quality metrics
    a_has_thesis = "THESIS:" in action_a.rationale.upper()
    b_has_thesis = "THESIS:" in action_b.rationale.upper()
    a_has_data = any(c.isdigit() for c in action_a.rationale[:100])
    b_has_data = any(c.isdigit() for c in action_b.rationale[:100])

    data("Rationale quality A", f"{'✅ has thesis' if a_has_thesis else '❌ no thesis'} | {'✅ cites data' if a_has_data else '❌ no data'}")
    data("Rationale quality B", f"{'✅ has thesis' if b_has_thesis else '❌ no thesis'} | {'✅ cites data' if b_has_data else '❌ no data'}")

    # Brain loop extras
    brain = meta_a.get("brain_loop", {})
    n_memos = len(brain.get("memos", []))
    n_contra = len(brain.get("thesis", {}).get("contradictions", []))
    data("Brain loop memos", str(n_memos))
    data("Contradictions found", str(n_contra))

    results = {
        "test": "ab_comparison",
        "passed": True,
        "checks": [],
        "brain_loop": {
            "action": action_a.action, "confidence": action_a.confidence,
            "elapsed_s": round(elapsed_a, 2), "turns": meta_a.get("turns", 0),
            "rationale_len": len(action_a.rationale),
            "has_thesis": a_has_thesis, "cites_data": a_has_data,
            "memos": n_memos, "contradictions": n_contra,
        },
        "legacy": {
            "action": action_b.action, "confidence": action_b.confidence,
            "elapsed_s": round(elapsed_b, 2), "turns": meta_b.get("turns", 0),
            "rationale_len": len(action_b.rationale),
            "has_thesis": b_has_thesis, "cites_data": b_has_data,
        },
    }

    # A/B quality score
    score_a = (
        (1 if a_has_thesis else 0) +
        (1 if a_has_data else 0) +
        (1 if n_memos >= 4 else 0) +
        (1 if n_contra > 0 else 0) +
        (1 if len(action_a.rationale) > 200 else 0)
    )
    score_b = (
        (1 if b_has_thesis else 0) +
        (1 if b_has_data else 0) +
        (0) +  # No memos in legacy
        (0) +  # No contradictions in legacy
        (1 if len(action_b.rationale) > 200 else 0)
    )
    data("Quality score A (brain loop)", f"{score_a}/5")
    data("Quality score B (legacy)", f"{score_b}/5")

    if score_a >= score_b:
        ok(f"Brain loop quality ({score_a}/5) ≥ legacy ({score_b}/5)")
    else:
        warn(f"Legacy quality ({score_b}/5) > brain loop ({score_a}/5)")

    results["quality_a"] = score_a
    results["quality_b"] = score_b
    return results


# ══════════════════════════════════════════════════════════════════
# Main runner
# ══════════════════════════════════════════════════════════════════

ALL_TESTS = {
    "hallucination": test_hallucination_injection,
    "contradiction": test_contradiction_forcing,
    "ablation": test_domain_ablation,
    "consistency": test_signal_consistency,
    "citation": test_citation_roundtrip,
    "ab": test_ab_comparison,
}


async def main():
    parser = argparse.ArgumentParser(description="Cross-Examination Benchmark Suite")
    parser.add_argument(
        "--test", type=str, default=None,
        choices=list(ALL_TESTS.keys()),
        help="Run a specific test (default: run all)",
    )
    args = parser.parse_args()

    print(f"\n{'═' * _W}")
    print(f"  CROSS-EXAMINATION & A/B BENCHMARK SUITE")
    print(f"  Model: gemma3:4b | Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═' * _W}")

    # Initialize DB
    from app.database import get_db
    get_db()

    tests_to_run = {args.test: ALL_TESTS[args.test]} if args.test else ALL_TESTS
    all_results = {}
    total_t0 = time.perf_counter()

    for name, func in tests_to_run.items():
        try:
            result = await func()
            all_results[name] = result
        except Exception as exc:
            fail(f"Test {name} CRASHED: {exc}")
            import traceback; traceback.print_exc()
            all_results[name] = {"test": name, "passed": False, "error": str(exc)}

    total_elapsed = time.perf_counter() - total_t0

    # ── Final Summary ─────────────────────────────────────────
    print(f"\n{'═' * _W}")
    print(f"  FINAL RESULTS — {total_elapsed:.1f}s total")
    print(f"{'═' * _W}\n")

    passed = 0
    failed = 0
    for name, result in all_results.items():
        status = "✅ PASS" if result.get("passed") else "❌ FAIL"
        elapsed = result.get("elapsed_s", 0)
        checks = result.get("checks", [])
        n_pass = len([c for c in checks if c.get("pass")])
        n_total = len(checks)
        print(f"  {status}  {name}: {n_pass}/{n_total} checks ({elapsed:.1f}s)")

        if result.get("passed"):
            passed += 1
        else:
            failed += 1

    print(f"\n  Total: {passed}/{passed + failed} tests passed")

    # Save results
    report_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "plan",
        "cross_exam_results.json",
    )
    with open(report_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"  Results saved: {report_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
