#!/usr/bin/env python3
"""
Full Pipeline Audit — validates ALL Python tools produced data after a trading run.

Usage:
    source venv/bin/activate
    python tests/full_pipeline_audit.py [--json]

Checks:
  Part 1 — DuckDB Table Census (row counts, empty tables)
  Part 2 — Per-Table Data Quality (nulls, date freshness, completeness)
  Part 3 — Pipeline Telemetry Analysis (which tools ran, failures, timing)
  Part 4 — Pipeline Events Audit (per-phase event coverage)
  Part 5 — Cross-Table Ticker Consistency (watchlist ↔ data tables)
  Part 6 — Tool Coverage Matrix (maps 55 services → expected tables)
  Part 7 — Summary Report
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb
from app.config import settings


def safe_connect():
    """Connect to DuckDB, falling back to a temp copy if the server holds the lock."""
    db_path = str(settings.DB_PATH)
    try:
        return duckdb.connect(db_path, read_only=True)
    except Exception:
        # Server holds the lock — copy to /tmp and read from there
        tmp_path = os.path.join(tempfile.gettempdir(), "audit_snapshot.duckdb")
        # Remove stale WAL/tmp files
        for suffix in ("", ".wal"):
            src = db_path + suffix
            dst = tmp_path + suffix
            if os.path.exists(src):
                shutil.copy2(src, dst)
        print("  ⚡ Server holds DB lock — reading from snapshot copy")
        return duckdb.connect(tmp_path, read_only=True)

# ──────────────────────────────────────────────────────────────────
# Infrastructure
# ──────────────────────────────────────────────────────────────────

class AuditResult:
    def __init__(self, name: str, passed: bool, details: str = "",
                 severity: str = "ERROR", data: dict | None = None):
        self.name = name
        self.passed = passed
        self.details = details
        self.severity = severity
        self.data = data or {}

RESULTS: list[AuditResult] = []

def audit(name: str, severity: str = "ERROR"):
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                passed, details, data = func(*args, **kwargs)
                RESULTS.append(AuditResult(name, passed, details, severity, data))
            except Exception as e:
                RESULTS.append(AuditResult(
                    name, False, f"Exception: {e}\n{traceback.format_exc()}", severity,
                ))
        wrapper._audit_name = name
        return wrapper
    return decorator


# ──────────────────────────────────────────────────────────────────
# Data tables that should contain data after a full pipeline run
# ──────────────────────────────────────────────────────────────────

# Map: table_name → description
DATA_TABLES = {
    # Core financial data (yfinance_service steps 1-9)
    "price_history":       "OHLCV candles (Step 1)",
    "fundamentals":        "Ticker .info snapshot (Step 2)",
    "financial_history":   "Multi-year income statement (Step 3)",
    "technicals":          "154 pandas-ta indicators (Step 4)",
    "balance_sheet":       "Multi-year balance sheet (Step 5)",
    "cash_flows":          "Multi-year cash flow (Step 6)",
    "analyst_data":        "Price targets + recommendations (Step 7)",
    "insider_activity":    "Insider transactions (Step 8)",
    "earnings_calendar":   "Next earnings date (Step 9)",
    # Derived/computed
    "risk_metrics":        "Quant risk metrics (Step 10)",
    "quant_scorecards":    "QuantSignalEngine flags (Step 10b)",
    # News & media
    "news_articles":       "yfinance + Google News + SEC EDGAR (Step 11)",
    "youtube_transcripts": "YouTube video transcripts (Step 12)",
    # Smart money
    "sec_13f_filers":      "SEC 13F filer registry",
    "sec_13f_holdings":    "Institutional 13F holdings (Step 14a)",
    "congressional_trades":"Congressional stock trades (Step 14b)",
    "news_full_articles":  "Full RSS news articles (Step 14c)",
    # Discovery & watchlist
    "discovered_tickers":  "Tickers found by discovery",
    "ticker_scores":       "Scored tickers from discovery",
    "watchlist":           "Active watchlist entries",
    # Analysis
    "ticker_dossiers":     "Deep analysis dossiers",
    "embeddings":          "Text embeddings for RAG",
    # Trading
    "positions":           "Open/closed positions",
    "orders":              "Order history",
    "trade_decisions":     "LLM trading decisions",
    "trade_executions":    "Executed trades",
    "portfolio_snapshots": "Daily portfolio snapshot",
    # Logging/audit
    "pipeline_events":     "Pipeline activity events",
    "pipeline_telemetry":  "Tool execution telemetry",
    "llm_audit_logs":      "LLM call audit trail",
    "llm_conversations":   "Conversation records",
    "pipeline_workflows":  "Workflow graph records",
    # System
    "bots":                "Registered bot models",
    "scheduler_runs":      "Scheduled task runs",
    "reports":             "Generated reports",
}

# Critical tables that MUST have data after any pipeline run
CRITICAL_TABLES = {
    "price_history", "fundamentals", "technicals",
    "pipeline_events", "watchlist",
}

# Tables that should have per-ticker data matching the watchlist
TICKER_DATA_TABLES = [
    "price_history", "fundamentals", "technicals",
    "financial_history", "balance_sheet", "cash_flows",
    "risk_metrics",
]

# Tool → expected DuckDB table mapping (for coverage matrix)
TOOL_TABLE_MAP = {
    "collect_price_history":    "price_history",
    "collect_fundamentals":     "fundamentals",
    "collect_financial_history": "financial_history",
    "compute_technicals":       "technicals",
    "collect_balance_sheet":    "balance_sheet",
    "collect_cashflow":         "cash_flows",
    "collect_analyst_data":     "analyst_data",
    "collect_insider_activity": "insider_activity",
    "collect_earnings_calendar":"earnings_calendar",
    "compute_risk_metrics":     "risk_metrics",
    "compute_quant_scorecard":  "quant_scorecards",
    "collect_news":             "news_articles",
    "collect_youtube":          "youtube_transcripts",
    "get_holdings_for_ticker":  "sec_13f_holdings",
    "get_trades_for_ticker":    "congressional_trades",
    "get_articles_for_ticker":  "news_full_articles",
    "save_dossier":             "ticker_dossiers",
    "save_embeddings":          "embeddings",
    "create_order":             "orders",
    "open_position":            "positions",
    "log_trade_execution":      "trade_executions",
    "record_trade_decision":    "trade_decisions",
    "snapshot_portfolio":       "portfolio_snapshots",
}


# ──────────────────────────────────────────────────────────────────
# Part 1: DuckDB Table Census
# ──────────────────────────────────────────────────────────────────

@audit("1.1 DuckDB Table Row Counts")
def check_table_census(conn):
    counts = {}
    empty = []
    for tbl in DATA_TABLES:
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            cnt = row[0] if row else 0
        except Exception:
            cnt = -1  # table doesn't exist
        counts[tbl] = cnt
        if cnt == 0:
            empty.append(tbl)

    total_rows = sum(c for c in counts.values() if c > 0)
    lines = [f"Total rows across {len(DATA_TABLES)} tables: {total_rows:,}"]
    if empty:
        lines.append(f"EMPTY tables ({len(empty)}): {', '.join(empty)}")
    lines.append("Top tables: " + ", ".join(
        f"{t}={c:,}" for t, c in sorted(counts.items(), key=lambda x: -x[1])[:8]
    ))

    passed = not any(t in CRITICAL_TABLES for t in empty)
    return passed, "\n    ".join(lines), {"counts": counts, "empty": empty}


@audit("1.2 Critical Tables Have Data")
def check_critical_tables(conn):
    missing = []
    for tbl in CRITICAL_TABLES:
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            if cnt == 0:
                missing.append(tbl)
        except Exception:
            missing.append(f"{tbl} (not found)")
    if missing:
        return False, f"Critical tables empty or missing: {missing}", {}
    return True, f"All {len(CRITICAL_TABLES)} critical tables have data", {}


# ──────────────────────────────────────────────────────────────────
# Part 2: Per-Table Data Quality
# ──────────────────────────────────────────────────────────────────

@audit("2.1 Price History Freshness")
def check_price_freshness(conn):
    row = conn.execute("""
        SELECT COUNT(DISTINCT ticker), MAX(date), MIN(date)
        FROM price_history
    """).fetchone()
    if not row or row[0] == 0:
        return False, "No price history data", {}
    tickers, latest, earliest = row
    details = f"{tickers} tickers, date range: {earliest} → {latest}"
    # Check if latest is within 3 days (weekend buffer)
    if latest:
        from datetime import date as dt_date
        latest_d = latest if isinstance(latest, dt_date) else dt_date.fromisoformat(str(latest))
        days_old = (dt_date.today() - latest_d).days
        details += f" ({days_old}d old)"
        if days_old > 5:
            return False, f"Price data is {days_old} days stale. {details}", {"days_old": days_old}
    return True, details, {"tickers": tickers}


@audit("2.2 Fundamentals Completeness", severity="WARNING")
def check_fundamentals_quality(conn):
    row = conn.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN market_cap = 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN sector = '' OR sector IS NULL THEN 1 ELSE 0 END)
        FROM fundamentals
    """).fetchone()
    if not row or row[0] == 0:
        return False, "No fundamentals data", {}
    total, no_mcap, no_sector = row
    issues = []
    if no_mcap > 0:
        issues.append(f"{no_mcap}/{total} missing market_cap")
    if no_sector > 0:
        issues.append(f"{no_sector}/{total} missing sector")
    details = f"{total} snapshots" + (f" — issues: {', '.join(issues)}" if issues else "")
    return len(issues) == 0, details, {"total": total}


@audit("2.3 Technicals Computed", severity="WARNING")
def check_technicals_quality(conn):
    row = conn.execute("""
        SELECT COUNT(DISTINCT ticker),
               COUNT(*),
               SUM(CASE WHEN rsi IS NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN macd IS NULL THEN 1 ELSE 0 END)
        FROM technicals
    """).fetchone()
    if not row or row[0] == 0:
        return False, "No technicals data", {}
    tickers, total, null_rsi, null_macd = row
    details = f"{tickers} tickers, {total} rows"
    if null_rsi > total * 0.3:
        details += f" — WARNING: {null_rsi}/{total} null RSI"
    return True, details, {"tickers": tickers, "total": total}


@audit("2.4 News Articles Collected")
def check_news_quality(conn):
    row = conn.execute("""
        SELECT COUNT(DISTINCT ticker), COUNT(*),
               COUNT(DISTINCT source)
        FROM news_articles
    """).fetchone()
    if not row or row[0] == 0:
        return False, "No news articles", {}
    tickers, total, sources = row
    return True, f"{total} articles for {tickers} tickers from {sources} sources", {}


@audit("2.5 YouTube Transcripts Collected", severity="WARNING")
def check_youtube_quality(conn):
    row = conn.execute("""
        SELECT COUNT(DISTINCT ticker), COUNT(*)
        FROM youtube_transcripts
    """).fetchone()
    if not row or row[0] == 0:
        return False, "No YouTube transcripts", {}
    tickers, total = row
    return True, f"{total} transcripts for {tickers} tickers", {}


# ──────────────────────────────────────────────────────────────────
# Part 3: Pipeline Telemetry Analysis
# ──────────────────────────────────────────────────────────────────

@audit("3.1 Pipeline Telemetry — Tool Coverage")
def check_telemetry_coverage(conn):
    try:
        rows = conn.execute("""
            SELECT step_name, COUNT(*) as cnt,
                   SUM(CASE WHEN status IN ('ok','success') THEN 1 ELSE 0 END) as ok_cnt,
                   SUM(CASE WHEN status NOT IN ('ok','success') THEN 1 ELSE 0 END) as fail_cnt,
                   AVG(duration_ms) as avg_ms
            FROM pipeline_telemetry
            GROUP BY step_name
            ORDER BY cnt DESC
        """).fetchall()
    except Exception:
        return False, "pipeline_telemetry table not found or empty", {}

    if not rows:
        return False, "No telemetry records found", {}

    tool_names = [r[0] for r in rows]
    total_invocations = sum(r[1] for r in rows)
    total_failures = sum(r[3] for r in rows)

    lines = [f"{len(tool_names)} unique tools invoked ({total_invocations} total calls, {total_failures} failures)"]
    for r in rows[:12]:
        icon = "✅" if r[3] == 0 else "⚠️"
        lines.append(f"  {icon} {r[0]}: {r[1]} calls ({r[2]} ok, {r[3]} fail, avg {r[4]:.0f}ms)")

    return total_failures < total_invocations * 0.5, "\n    ".join(lines), {
        "tools": tool_names, "total": total_invocations, "failures": total_failures,
    }


@audit("3.2 Pipeline Telemetry — Slow Steps (>30s)", severity="WARNING")
def check_slow_steps(conn):
    try:
        rows = conn.execute("""
            SELECT step_name, ticker, duration_ms, status
            FROM pipeline_telemetry
            WHERE duration_ms > 30000
            ORDER BY duration_ms DESC
            LIMIT 10
        """).fetchall()
    except Exception:
        return True, "pipeline_telemetry not available", {}
    if not rows:
        return True, "No steps exceeded 30s", {}
    lines = [f"{r[0]} ({r[1]}): {r[2]/1000:.1f}s — {r[3]}" for r in rows]
    return False, f"{len(rows)} slow steps:\n    " + "\n    ".join(lines), {}


# ──────────────────────────────────────────────────────────────────
# Part 4: Pipeline Events Audit
# ──────────────────────────────────────────────────────────────────

@audit("4.1 Pipeline Events — Phase Coverage")
def check_pipeline_events(conn):
    try:
        rows = conn.execute("""
            SELECT phase, COUNT(*), COUNT(DISTINCT event_type)
            FROM pipeline_events
            GROUP BY phase
            ORDER BY COUNT(*) DESC
        """).fetchall()
    except Exception:
        return False, "pipeline_events table not found", {}
    if not rows:
        return False, "No pipeline events recorded", {}
    lines = [f"{r[0]}: {r[1]} events, {r[2]} types" for r in rows]
    phases = [r[0] for r in rows]
    return True, f"{len(phases)} phases logged:\n    " + "\n    ".join(lines), {"phases": phases}


@audit("4.2 Pipeline Events — Recent Activity")
def check_recent_events(conn):
    try:
        row = conn.execute("""
            SELECT COUNT(*), MAX(timestamp), MIN(timestamp)
            FROM pipeline_events
            WHERE timestamp > CURRENT_TIMESTAMP - INTERVAL '24 hours'
        """).fetchone()
    except Exception:
        return True, "Cannot check recent events", {}
    if not row or row[0] == 0:
        return False, "No pipeline events in last 24 hours", {}
    return True, f"{row[0]} events in last 24h (range: {row[2]} → {row[1]})", {}


# ──────────────────────────────────────────────────────────────────
# Part 5: Cross-Table Ticker Consistency
# ──────────────────────────────────────────────────────────────────

# Tables that only apply to individual stocks, NOT ETFs/funds
ETF_SKIP_TABLES = {"financial_history", "balance_sheet", "cash_flows"}

@audit("5.1 Watchlist Tickers Have Data in Core Tables", severity="WARNING")
def check_ticker_consistency(conn):
    try:
        watchlist = conn.execute("""
            SELECT DISTINCT ticker FROM watchlist WHERE status = 'active'
        """).fetchall()
    except Exception:
        return True, "No watchlist table", {}

    if not watchlist:
        return True, "Watchlist is empty — no consistency check needed", {}

    tickers = [w[0].replace("$", "") for w in watchlist]

    # Detect ETFs: they have fundamentals but empty/null quoteType or sector=''
    etf_tickers = set()
    try:
        etf_rows = conn.execute("""
            SELECT DISTINCT ticker FROM fundamentals
            WHERE (sector IS NULL OR sector = '' OR sector = 'N/A')
              AND (industry IS NULL OR industry = '' OR industry = 'N/A')
        """).fetchall()
        etf_tickers = {r[0] for r in etf_rows}
    except Exception:
        pass

    coverage = {}
    for ticker in tickers:
        ticker_cov = {}
        for tbl in TICKER_DATA_TABLES:
            # Skip financial tables for ETFs — they don't have income statements
            if ticker in etf_tickers and tbl in ETF_SKIP_TABLES:
                ticker_cov[tbl] = -2  # -2 = skipped (ETF)
                continue
            try:
                cnt = conn.execute(
                    f"SELECT COUNT(*) FROM {tbl} WHERE ticker = ?", [ticker]
                ).fetchone()[0]
                ticker_cov[tbl] = cnt
            except Exception:
                ticker_cov[tbl] = -1
        coverage[ticker] = ticker_cov

    # Report gaps (skip ETF-excluded tables)
    gaps = []
    for ticker, cov in coverage.items():
        missing = [t for t, c in cov.items() if c == 0]  # 0 = should have data but doesn't
        if missing:
            gaps.append(f"${ticker}: missing data in {', '.join(missing)}")

    etf_list = [t for t in tickers if t in etf_tickers]
    lines = [f"{len(tickers)} watchlist tickers checked against {len(TICKER_DATA_TABLES)} tables"]
    if etf_list:
        lines.append(f"ETFs detected (financial tables skipped): {', '.join(etf_list)}")
    if gaps:
        lines.append(f"GAPS ({len(gaps)} tickers with missing data):")
        for g in gaps[:8]:
            lines.append(f"  ⚠️ {g}")
    else:
        lines.append("All tickers have data in all applicable tables ✅")

    return len(gaps) == 0, "\n    ".join(lines), {"coverage": coverage, "gaps": gaps, "etfs": etf_list}


# ──────────────────────────────────────────────────────────────────
# Part 6: Tool Coverage Matrix
# ──────────────────────────────────────────────────────────────────

@audit("6.1 Tool → Table Coverage Matrix")
def check_tool_coverage(conn):
    covered = []
    uncovered = []
    for tool, table in TOOL_TABLE_MAP.items():
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if cnt > 0:
                covered.append(f"✅ {tool} → {table} ({cnt:,} rows)")
            else:
                uncovered.append(f"❌ {tool} → {table} (0 rows)")
        except Exception:
            uncovered.append(f"❌ {tool} → {table} (table not found)")

    total = len(TOOL_TABLE_MAP)
    lines = [f"{len(covered)}/{total} tools produced data"]
    if uncovered:
        lines.append(f"Missing coverage ({len(uncovered)}):")
        for u in uncovered:
            lines.append(f"  {u}")

    rate = len(covered) / total if total > 0 else 0
    return rate >= 0.5, "\n    ".join(lines), {
        "coverage_rate": rate, "covered": len(covered), "uncovered": len(uncovered),
    }


@audit("6.2 Pipeline Telemetry Tool Names vs Expected", severity="WARNING")
def check_telemetry_vs_expected(conn):
    try:
        rows = conn.execute("""
            SELECT DISTINCT step_name FROM pipeline_telemetry
        """).fetchall()
    except Exception:
        return True, "pipeline_telemetry not available", {}

    # Telemetry uses "ClassName.method_name" format — extract method names
    telemetry_tools = set()
    for r in rows:
        name = r[0]
        if "." in name:
            telemetry_tools.add(name.split(".")[-1])
        telemetry_tools.add(name.lower())

    # Split expected tools: collection tools MUST appear, execution tools only if trades happened
    collection_tools = {
        "collect_price_history", "collect_fundamentals", "collect_financial_history",
        "compute_technicals", "collect_balance_sheet", "collect_cashflow",
        "collect_analyst_data", "collect_insider_activity", "collect_earnings_calendar",
        "compute_risk_metrics", "compute_quant_scorecard",
        "collect_news", "collect_youtube",
        "get_holdings_for_ticker", "get_trades_for_ticker", "get_articles_for_ticker",
    }
    execution_tools = {
        "save_dossier", "save_embeddings", "create_order", "open_position",
        "log_trade_execution", "record_trade_decision", "snapshot_portfolio",
    }

    def _match(expected):
        bare = expected.replace("collect_", "").replace("compute_", "")
        for logged in telemetry_tools:
            logged_l = logged.lower()
            if (expected == logged_l or bare in logged_l
                    or logged_l in expected or expected in logged_l):
                return True
        return False

    missing_collection = [t for t in collection_tools if not _match(t)]
    missing_execution = [t for t in execution_tools if not _match(t)]

    lines = []
    if missing_collection:
        lines.append(f"Collection tools not in telemetry ({len(missing_collection)}): {sorted(missing_collection)}")
    if missing_execution:
        lines.append(f"Execution tools not yet invoked ({len(missing_execution)}): {sorted(missing_execution)} (expected — runs after trades happen)")
    if not lines:
        lines.append(f"All expected tools found in telemetry")

    # Only fail if collection tools are missing (execution tools are optional pre-run)
    return len(missing_collection) == 0, "\n    ".join(lines), {
        "missing_collection": missing_collection, "missing_execution": missing_execution,
    }


# ──────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────

ALL_CHECKS = [
    # Part 1: Census
    check_table_census,
    check_critical_tables,
    # Part 2: Quality
    check_price_freshness,
    check_fundamentals_quality,
    check_technicals_quality,
    check_news_quality,
    check_youtube_quality,
    # Part 3: Telemetry
    check_telemetry_coverage,
    check_slow_steps,
    # Part 4: Events
    check_pipeline_events,
    check_recent_events,
    # Part 5: Consistency
    check_ticker_consistency,
    # Part 6: Coverage
    check_tool_coverage,
    check_telemetry_vs_expected,
]


def snapshot_baseline():
    """Run full audit AND save baseline snapshot before a trading run."""
    # Run the full audit first so you see all errors
    run_audit(output_json=False)

    # Then save the row-count snapshot for post-run comparison
    conn = safe_connect()
    counts = {}
    for tbl in DATA_TABLES:
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        except Exception:
            cnt = -1
        counts[tbl] = cnt
    conn.close()

    report_dir = settings.BASE_DIR / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = report_dir / "audit_baseline.json"
    baseline = {
        "timestamp": datetime.now().isoformat(),
        "counts": counts,
    }
    baseline_path.write_text(json.dumps(baseline, indent=2))
    total = sum(c for c in counts.values() if c > 0)
    print(f"\n  📸  Baseline snapshot saved ({total:,} total rows)")
    print(f"      {baseline_path}")
    print(f"      Run --compare after the trading run to see what changed\n")


def compare_with_baseline():
    """Compare current state against the saved baseline to see what changed."""
    baseline_path = settings.BASE_DIR / "reports" / "audit_baseline.json"
    if not baseline_path.exists():
        print("\n  ❌  No baseline found. Run with --baseline first.\n")
        return

    baseline = json.loads(baseline_path.read_text())
    conn = safe_connect()

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║        PIPELINE DIFF — baseline vs current                  ║
║  Baseline: {baseline['timestamp'][:30]:<46}  ║
╚══════════════════════════════════════════════════════════════╝
""")
    changes = []
    for tbl in DATA_TABLES:
        try:
            current = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        except Exception:
            current = -1
        prev = baseline["counts"].get(tbl, 0)
        delta = current - prev
        if delta != 0:
            icon = "🟢" if delta > 0 else "🔴"
            changes.append((tbl, prev, current, delta, icon))

    if changes:
        print(f"  {len(changes)} tables changed:\n")
        for tbl, prev, cur, delta, icon in sorted(changes, key=lambda x: -abs(x[3])):
            print(f"    {icon} {tbl}: {prev:,} → {cur:,} ({'+' if delta > 0 else ''}{delta:,})")
    else:
        print("  No changes detected since baseline.")

    conn.close()
    print()


def run_audit(output_json: bool = False):
    conn = safe_connect()

    print("""
╔══════════════════════════════════════════════════════════════╗
║           FULL PIPELINE AUDIT — ALL PYTHON TOOLS            ║
╚══════════════════════════════════════════════════════════════╝
""")

    for check in ALL_CHECKS:
        check(conn)

    # Print results
    passed = sum(1 for r in RESULTS if r.passed)
    failed = sum(1 for r in RESULTS if not r.passed and r.severity == "ERROR")
    warns = sum(1 for r in RESULTS if not r.passed and r.severity == "WARNING")
    total = len(RESULTS)

    for r in RESULTS:
        icon = "✅" if r.passed else ("⚠️ " if r.severity == "WARNING" else "❌")
        print(f"  {icon}  {r.name}")
        if r.details:
            for line in r.details.split("\n"):
                print(f"       {line}")
        print()

    print("─" * 62)
    print(f"  Results: {passed}/{total} passed | {failed} errors | {warns} warnings")
    print("─" * 62)

    if failed > 0:
        print("\n  ⛔  AUDIT FAILED — review errors above\n")
    elif warns > 0:
        print("\n  ⚠️  AUDIT PASSED WITH WARNINGS\n")
    else:
        print("\n  ✅  AUDIT PASSED — pipeline is healthy\n")

    # Save JSON report
    if output_json:
        report = {
            "timestamp": datetime.now().isoformat(),
            "summary": {"passed": passed, "failed": failed, "warnings": warns, "total": total},
            "checks": [
                {
                    "name": r.name, "passed": r.passed,
                    "details": r.details, "severity": r.severity, "data": r.data,
                }
                for r in RESULTS
            ],
        }
        report_dir = settings.BASE_DIR / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        report_path = report_dir / f"audit_{ts}.json"
        report_path.write_text(json.dumps(report, indent=2, default=str))
        print(f"  JSON report saved to: {report_path}\n")

    conn.close()
    return failed == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Full pipeline audit — all Python tools")
    parser.add_argument("--json", action="store_true", help="Save JSON report to reports/")
    parser.add_argument("--baseline", action="store_true",
                        help="Save baseline snapshot BEFORE a trading run")
    parser.add_argument("--compare", action="store_true",
                        help="Compare current state vs baseline AFTER a trading run")
    args = parser.parse_args()

    if args.baseline:
        snapshot_baseline()
    elif args.compare:
        compare_with_baseline()
    else:
        success = run_audit(output_json=args.json)
        sys.exit(0 if success else 1)

