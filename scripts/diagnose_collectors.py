"""End-to-end data collection diagnostic.

Runs each collector independently for a given ticker, prints a clear
report card, and verifies data landed in the database.

Usage:
    python scripts/diagnose_collectors.py NVDA
    python scripts/diagnose_collectors.py AAPL --skip-youtube
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone

# ── Bootstrap ──────────────────────────────────────────────────
# Ensure we can import from the app package
sys.path.insert(0, ".")

from app.database import get_db  # noqa: E402


# ── Styling helpers ────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str) -> str:
    return f"  {GREEN}✓{RESET} {msg}"


def fail(msg: str) -> str:
    return f"  {RED}✗{RESET} {msg}"


def warn(msg: str) -> str:
    return f"  {YELLOW}⚠{RESET} {msg}"


def header(msg: str) -> None:
    print(f"\n{CYAN}{BOLD}{'─' * 60}{RESET}")
    print(f"{CYAN}{BOLD}  {msg}{RESET}")
    print(f"{CYAN}{BOLD}{'─' * 60}{RESET}")


def section(msg: str) -> None:
    print(f"\n{BOLD}▶ {msg}{RESET}")


# ── Collector runners ──────────────────────────────────────────

async def run_step(name: str, coro, results: list) -> object:
    """Run a collector step, print result, and record pass/fail."""
    t0 = time.perf_counter()
    try:
        data = await coro
        elapsed = time.perf_counter() - t0

        # Figure out the count
        if isinstance(data, list):
            count = len(data)
            detail = f"{count} rows/items"
        elif data is None:
            count = 0
            detail = "None returned"
        else:
            count = 1
            detail = type(data).__name__

        if count > 0:
            print(ok(f"{name}: {detail} ({elapsed:.1f}s)"))
            results.append((name, "PASS", detail))
        else:
            print(warn(f"{name}: empty result ({elapsed:.1f}s)"))
            results.append((name, "EMPTY", detail))

        return data

    except Exception as e:
        elapsed = time.perf_counter() - t0
        error_msg = str(e)[:120]
        print(fail(f"{name}: {error_msg} ({elapsed:.1f}s)"))
        results.append((name, "FAIL", error_msg))
        return None


async def diagnose(ticker: str, *, skip_youtube: bool = False) -> None:
    """Run all collectors for a ticker and print a diagnostic report."""

    header(f"DATA COLLECTION DIAGNOSTIC: {ticker}")
    print(f"  Time: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Skip YouTube: {skip_youtube}")

    results: list[tuple[str, str, str]] = []

    # ── 1. YFinance Collector ──────────────────────────────────
    from app.collectors.yfinance_collector import YFinanceCollector
    yf = YFinanceCollector()

    section("YFinance Data (Steps 1-9)")

    price_history = await run_step(
        "1. Price History (6mo OHLCV)", yf.collect_price_history(ticker), results
    )
    await run_step(
        "2. Fundamentals (.info)", yf.collect_fundamentals(ticker), results
    )
    await run_step(
        "3. Financial History (income stmt)", yf.collect_financial_history(ticker), results
    )
    await run_step(
        "5. Balance Sheet", yf.collect_balance_sheet(ticker), results
    )
    await run_step(
        "6. Cash Flow", yf.collect_cashflow(ticker), results
    )
    await run_step(
        "7. Analyst Data", yf.collect_analyst_data(ticker), results
    )
    await run_step(
        "8. Insider Activity", yf.collect_insider_activity(ticker), results
    )
    await run_step(
        "9. Earnings Calendar", yf.collect_earnings_calendar(ticker), results
    )

    # ── 2. Technical Indicators ────────────────────────────────
    section("Technical Indicators (Step 4)")

    if price_history:
        from app.collectors.technical_computer import TechnicalComputer
        tc = TechnicalComputer()
        technicals = await run_step(
            "4. Technical Indicators (pandas-ta)", tc.compute(ticker), results
        )
        if technicals and len(technicals) > 0:
            last = technicals[-1]
            # Print a few key indicators from the latest row
            print(f"     Latest: RSI={getattr(last, 'rsi', '?'):.1f}"
                  f"  MACD={getattr(last, 'macd', '?'):.2f}"
                  f"  SMA20={getattr(last, 'sma_20', '?'):.2f}")
    else:
        print(warn("4. Technical Indicators: SKIPPED (no price data)"))
        results.append(("4. Technical Indicators", "SKIP", "no price data"))

    # ── 3. Risk Computer ───────────────────────────────────────
    section("Risk Metrics (Step 10)")

    if price_history:
        from app.collectors.risk_computer import RiskComputer
        rc = RiskComputer()
        risk = await run_step(
            "10. Risk Metrics (25+ quant)", rc.compute(ticker), results
        )
        if risk:
            print(f"     Sharpe={risk.sharpe_ratio:.2f}"
                  f"  VaR95={risk.var_95:.4f}"
                  f"  MaxDD={risk.max_drawdown:.2%}"
                  f"  Beta={risk.beta:.2f}")
    else:
        print(warn("10. Risk Metrics: SKIPPED (no price data)"))
        results.append(("10. Risk Metrics", "SKIP", "no price data"))

    # ── 4. News Collector ──────────────────────────────────────
    section("News Collection (Step 11)")

    from app.collectors.news_collector import NewsCollector
    nc = NewsCollector()

    await run_step(
        "11a. News Scrape (fresh)", nc.collect(ticker), results
    )

    all_news = await run_step(
        "11b. News Historical (all DB)", nc.get_all_historical(ticker), results
    )

    if all_news:
        # Show source breakdown
        sources: dict[str, int] = {}
        for a in all_news:
            sources[a.source] = sources.get(a.source, 0) + 1
        source_str = ", ".join(f"{k}={v}" for k, v in sorted(sources.items()))
        print(f"     Sources: {source_str}")
        # Preview last 3
        print("     Recent articles:")
        for a in all_news[:3]:
            date_str = a.published_at.strftime("%m/%d") if a.published_at else "?"
            print(f"       [{date_str}] [{a.source}] {a.title[:70]}")

    # ── 5. YouTube Collector ───────────────────────────────────
    if not skip_youtube:
        section("YouTube Collection (Step 12)")

        from app.collectors.youtube_collector import YouTubeCollector
        yt = YouTubeCollector()

        await run_step(
            "12a. YouTube Scrape (24h)", yt.collect(ticker), results
        )

        all_transcripts = await run_step(
            "12b. YouTube Historical (all DB)", yt.get_all_historical(ticker), results
        )

        if all_transcripts:
            print(f"     Total transcripts in DB: {len(all_transcripts)}")
            for t in all_transcripts[:3]:
                transcript_len = len(t.raw_transcript) if t.raw_transcript else 0
                date_str = t.published_at.strftime("%m/%d") if t.published_at else "?"
                print(f"       [{date_str}] {t.channel}: {t.title[:50]}  ({transcript_len:,} chars)")
    else:
        print(warn("\n  YouTube: SKIPPED (--skip-youtube flag)"))
        results.append(("12a. YouTube Scrape", "SKIP", "user skipped"))
        results.append(("12b. YouTube Historical", "SKIP", "user skipped"))

    # ── 6. Database Verification ───────────────────────────────
    section("Database Verification")

    db = get_db()
    tables_to_check = [
        ("price_history", "ticker"),
        ("fundamentals", "ticker"),
        ("financial_history", "ticker"),
        ("technicals", "ticker"),
        ("news_articles", "ticker"),
        ("youtube_transcripts", "ticker"),
        ("risk_metrics", "ticker"),
        ("balance_sheet", "ticker"),
        ("cash_flows", "ticker"),
        ("analyst_data", "ticker"),
        ("insider_activity", "ticker"),
        ("earnings_calendar", "ticker"),
    ]

    for table, col in tables_to_check:
        try:
            row = db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", [ticker]
            ).fetchone()
            count = row[0] if row else 0
            if count > 0:
                print(ok(f"{table}: {count} rows"))
            else:
                print(warn(f"{table}: 0 rows"))
        except Exception as e:
            print(fail(f"{table}: {e}"))

    # ── Report Card ────────────────────────────────────────────
    header("REPORT CARD")

    passed = sum(1 for _, s, _ in results if s == "PASS")
    failed = sum(1 for _, s, _ in results if s == "FAIL")
    empty = sum(1 for _, s, _ in results if s == "EMPTY")
    skipped = sum(1 for _, s, _ in results if s == "SKIP")
    total = len(results)

    print(f"  {GREEN}PASS:    {passed}/{total}{RESET}")
    if empty:
        print(f"  {YELLOW}EMPTY:   {empty}/{total}{RESET}")
    if skipped:
        print(f"  {YELLOW}SKIPPED: {skipped}/{total}{RESET}")
    if failed:
        print(f"  {RED}FAILED:  {failed}/{total}{RESET}")
        print(f"\n  {RED}Failed steps:{RESET}")
        for name, status, detail in results:
            if status == "FAIL":
                print(f"    {RED}✗ {name}: {detail}{RESET}")

    if failed == 0:
        print(f"\n  {GREEN}{BOLD}🎉 ALL COLLECTORS WORKING!{RESET}")
    else:
        print(f"\n  {YELLOW}{BOLD}⚠ Some collectors need attention.{RESET}")

    print()

    # ── Generate audit report ──────────────────────────────────
    report_path = generate_audit_report(ticker, results, db)
    print(f"  {CYAN}📄 Audit report: {report_path}{RESET}\n")


def generate_audit_report(
    ticker: str,
    results: list[tuple[str, str, str]],
    db,
) -> str:
    """Generate a Markdown audit report with data samples."""
    from pathlib import Path

    now = datetime.now(tz=timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    report_dir = Path("reports")
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / f"{ticker}_audit_{date_str}.md"

    lines: list[str] = []
    lines.append(f"# {ticker} Data Audit Report")
    lines.append("")
    lines.append(f"**Generated:** {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Ticker:** {ticker}")
    lines.append("")

    # ── Collector Results Table ──
    passed = sum(1 for _, s, _ in results if s == "PASS")
    failed = sum(1 for _, s, _ in results if s == "FAIL")
    empty = sum(1 for _, s, _ in results if s == "EMPTY")
    skipped = sum(1 for _, s, _ in results if s == "SKIP")
    total = len(results)

    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| ✅ Pass | {passed}/{total} |")
    lines.append(f"| ❌ Fail | {failed}/{total} |")
    lines.append(f"| ⚠️ Empty | {empty}/{total} |")
    lines.append(f"| ⏭️ Skip | {skipped}/{total} |")
    lines.append("")

    lines.append("## Collector Results")
    lines.append("")
    lines.append("| Step | Status | Detail |")
    lines.append("|------|--------|--------|")
    for name, status, detail in results:
        icon = {"PASS": "✅", "FAIL": "❌", "EMPTY": "⚠️", "SKIP": "⏭️"}.get(status, "❓")
        lines.append(f"| {name} | {icon} {status} | {detail} |")
    lines.append("")

    # ── Database Row Counts ──
    lines.append("## Database Row Counts")
    lines.append("")
    lines.append("| Table | Rows |")
    lines.append("|-------|------|")

    tables = [
        "price_history", "fundamentals", "financial_history", "technicals",
        "news_articles", "youtube_transcripts", "risk_metrics",
        "balance_sheet", "cash_flows", "analyst_data",
        "insider_activity", "earnings_calendar",
    ]
    for table in tables:
        try:
            row = db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE ticker = ?", [ticker]
            ).fetchone()
            count = row[0] if row else 0
            lines.append(f"| {table} | {count} |")
        except Exception:
            lines.append(f"| {table} | ERROR |")
    lines.append("")

    # ── Price Data Sample ──
    lines.append("## Price Data (Last 5 Rows)")
    lines.append("")
    try:
        prices = db.execute(
            """SELECT date, open, high, low, close, volume
               FROM price_history WHERE ticker = ?
               ORDER BY date DESC LIMIT 5""",
            [ticker],
        ).fetchall()
        if prices:
            lines.append("| Date | Open | High | Low | Close | Volume |")
            lines.append("|------|------|------|-----|-------|--------|")
            for p in prices:
                lines.append(
                    f"| {p[0]} | {p[1]:.2f} | {p[2]:.2f} | {p[3]:.2f} | {p[4]:.2f} | {p[5]:,.0f} |"
                )
        else:
            lines.append("*No price data found.*")
    except Exception as e:
        lines.append(f"*Error reading price data: {e}*")
    lines.append("")

    # ── Technical Indicators Sample ──
    lines.append("## Technical Indicators (Latest Row)")
    lines.append("")
    try:
        tech = db.execute(
            """SELECT date, rsi, macd, macd_signal, macd_hist,
                      sma_20, sma_50, sma_200, bb_upper, bb_lower,
                      atr, stoch_k, stoch_d,
                      ema_9, ema_21, adx, cci, willr, mfi, obv
               FROM technicals WHERE ticker = ?
               ORDER BY date DESC LIMIT 1""",
            [ticker],
        ).fetchone()
        if tech:
            lines.append("| Indicator | Value |")
            lines.append("|-----------|-------|")
            labels = [
                "Date", "RSI", "MACD", "MACD Signal", "MACD Hist",
                "SMA 20", "SMA 50", "SMA 200", "BB Upper", "BB Lower",
                "ATR", "Stoch K", "Stoch D",
                "EMA 9", "EMA 21", "ADX", "CCI", "Williams %R", "MFI", "OBV",
            ]
            for label, val in zip(labels, tech):
                if val is not None:
                    if isinstance(val, float):
                        lines.append(f"| {label} | {val:.4f} |")
                    else:
                        lines.append(f"| {label} | {val} |")
                else:
                    lines.append(f"| {label} | — |")
        else:
            lines.append("*No technical data found.*")
    except Exception as e:
        lines.append(f"*Error reading technicals: {e}*")
    lines.append("")

    # ── Risk Metrics ──
    lines.append("## Risk Metrics")
    lines.append("")
    try:
        risk = db.execute(
            """SELECT sharpe_ratio, sortino_ratio, max_drawdown, beta,
                      var_95, cvar_95, annualized_volatility, alpha
               FROM risk_metrics WHERE ticker = ?
               ORDER BY computed_date DESC LIMIT 1""",
            [ticker],
        ).fetchone()
        if risk:
            labels = [
                "Sharpe Ratio", "Sortino Ratio", "Max Drawdown", "Beta",
                "VaR 95%", "CVaR 95%", "Annualized Volatility", "Alpha",
            ]
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            for label, val in zip(labels, risk):
                if val is not None:
                    lines.append(f"| {label} | {val:.4f} |")
                else:
                    lines.append(f"| {label} | — |")
        else:
            lines.append("*No risk metrics found.*")
    except Exception as e:
        lines.append(f"*Error reading risk metrics: {e}*")
    lines.append("")

    # ── News Headlines ──
    lines.append("## Recent News Headlines (Last 10)")
    lines.append("")
    try:
        news = db.execute(
            """SELECT published_at, source, title, publisher
               FROM news_articles WHERE ticker = ?
               ORDER BY published_at DESC NULLS LAST LIMIT 10""",
            [ticker],
        ).fetchall()
        if news:
            lines.append("| Date | Source | Title | Publisher |")
            lines.append("|------|--------|-------|-----------|")
            for n in news:
                date_str = str(n[0])[:10] if n[0] else "?"
                lines.append(f"| {date_str} | {n[1]} | {n[2][:60]} | {n[3]} |")
        else:
            lines.append("*No news articles found.*")
    except Exception as e:
        lines.append(f"*Error reading news: {e}*")
    lines.append("")

    # ── YouTube Transcripts ──
    lines.append("## YouTube Transcripts")
    lines.append("")
    try:
        yt = db.execute(
            """SELECT published_at, channel, title,
                      LENGTH(raw_transcript) as chars
               FROM youtube_transcripts WHERE ticker = ?
               ORDER BY published_at DESC LIMIT 5""",
            [ticker],
        ).fetchall()
        if yt:
            lines.append("| Date | Channel | Title | Chars |")
            lines.append("|------|---------|-------|-------|")
            for t in yt:
                date_str = str(t[0])[:10] if t[0] else "?"
                lines.append(f"| {date_str} | {t[1]} | {t[2][:50]} | {t[3]:,} |")
        else:
            lines.append("*No YouTube transcripts found.*")
    except Exception as e:
        lines.append(f"*Error reading YouTube data: {e}*")
    lines.append("")

    # ── Warnings / Errors ──
    failures = [(n, d) for n, s, d in results if s in ("FAIL", "EMPTY")]
    if failures:
        lines.append("## ⚠️ Warnings & Errors")
        lines.append("")
        for name, detail in failures:
            lines.append(f"- **{name}**: {detail}")
        lines.append("")

    lines.append("---")
    lines.append("*Report generated by `scripts/diagnose_collectors.py`*")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)


# ── CLI Entry ──────────────────────────────────────────────────

if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    skip_yt = "--skip-youtube" in sys.argv

    print(f"\n{BOLD}Running full diagnostic for {ticker}...{RESET}")
    asyncio.run(diagnose(ticker, skip_youtube=skip_yt))
