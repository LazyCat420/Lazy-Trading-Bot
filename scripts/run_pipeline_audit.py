#!/usr/bin/env python3
"""Pipeline Audit Runner — exercise each phase with MAXIMUM verbosity.

Usage:
    python scripts/run_pipeline_audit.py --phase embedding
    python scripts/run_pipeline_audit.py --phase analysis
    python scripts/run_pipeline_audit.py --phase trading
    python scripts/run_pipeline_audit.py --phase all

Runs against TEST database. Seed first: python scripts/seed_test_db.py
Model forced to gemma3:4b for initial debugging.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Project root ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Force test DB profile BEFORE any app imports
import os
os.environ["DB_PROFILE"] = "test"

from app.config import settings
settings.DB_PROFILE = "test"

# Force model to gemma3:4b for consistent testing
TEST_MODEL = "gemma3:4b"
settings.LLM_MODEL = TEST_MODEL

# ══════════════════════════════════════════════════════════════
# Formatting
# ══════════════════════════════════════════════════════════════
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"
RESET = "\033[0m"

BENCHMARKS: list[dict] = []  # Collect all timings for final summary


def header(phase: str, desc: str) -> None:
    print(f"\n{BOLD}{CYAN}{'═' * 72}")
    print(f"  PHASE: {phase.upper()}")
    print(f"  {desc}")
    print(f"{'═' * 72}{RESET}\n")


def section(title: str) -> None:
    print(f"\n{BOLD}{MAGENTA}  ── {title} ──{RESET}")


def sub(title: str) -> None:
    print(f"  {DIM}  ▸ {title}{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}✅ {msg}{RESET}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠️  {msg}{RESET}")


def fail(msg: str) -> None:
    print(f"  {RED}❌ {msg}{RESET}")


def info(msg: str) -> None:
    print(f"  {DIM}   {msg}{RESET}")


def data(label: str, value, max_len: int = 300) -> None:
    s = str(value)
    if len(s) > max_len:
        s = s[:max_len] + "…"
    print(f"  {BOLD}{label}:{RESET} {s}")


def bench_start(name: str) -> float:
    sub(f"⏱  Starting: {name}")
    return time.perf_counter()


def bench_end(name: str, t0: float, phase: str = "") -> float:
    elapsed = time.perf_counter() - t0
    label = f"{phase}/{name}" if phase else name
    BENCHMARKS.append({"step": label, "ms": round(elapsed * 1000, 1)})
    if elapsed > 5.0:
        warn(f"⏱  {name}: {elapsed:.2f}s (SLOW)")
    elif elapsed > 1.0:
        info(f"⏱  {name}: {elapsed:.2f}s")
    else:
        info(f"⏱  {name}: {elapsed * 1000:.0f}ms")
    return elapsed


def table_count(conn, table_name: str) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    except Exception:
        return -1


def db_rows(conn, query: str, params=None) -> list[dict]:
    """Execute query and return list of dicts."""
    try:
        if params:
            result = conn.execute(query, params)
        else:
            result = conn.execute(query)
        cols = [d[0] for d in result.description]
        return [dict(zip(cols, r)) for r in result.fetchall()]
    except Exception as exc:
        fail(f"Query failed: {exc}")
        return []


# ══════════════════════════════════════════════════════════════
# PHASE: DB STATUS
# ══════════════════════════════════════════════════════════════
def audit_db_status(conn) -> None:
    header("DB STATUS", "Verifying test database contents")
    tables = [
        "watchlist", "price_history", "technicals", "fundamentals",
        "financial_history", "balance_sheet", "cash_flows", "risk_metrics",
        "analyst_data", "insider_activity", "earnings_calendar",
        "news_articles", "news_full_articles",
        "youtube_transcripts", "youtube_trading_data",
        "sec_13f_holdings", "congressional_trades",
        "discovered_tickers", "ticker_scores",
        "trade_decisions", "portfolio_snapshots",
        "quant_scorecards", "ticker_dossiers", "embeddings",
    ]
    section("Row counts per table")
    total = 0
    empty = []
    for t in tables:
        count = table_count(conn, t)
        if count < 0:
            info(f"  {t}: (table not found)")
        elif count == 0:
            empty.append(t)
            info(f"  {t}: 0 rows")
        else:
            ok(f"{t}: {count} rows")
            total += count

    if empty:
        section("Empty tables")
        for t in empty:
            warn(t)

    data("Total rows", total)

    section("Active watchlist tickers")
    rows = db_rows(conn, "SELECT ticker, status, discovery_score, sentiment_hint FROM watchlist")
    for r in rows:
        ok(f"${r['ticker']}: status={r['status']}, score={r['discovery_score']}, sentiment={r['sentiment_hint']}")


# ══════════════════════════════════════════════════════════════
# PHASE: EMBEDDING
# ══════════════════════════════════════════════════════════════
async def audit_embedding(conn) -> dict:
    header("EMBEDDING", "Embed seeded data for RAG retrieval")
    from app.services.embedding_service import EmbeddingService
    svc = EmbeddingService()
    data("Embed model", svc.model)

    # Step 1: Check model
    section("Step 1: Check Ollama embedding model")
    t0 = bench_start("ensure_model_loaded")
    model_ok = await svc.ensure_model_loaded()
    bench_end("ensure_model_loaded", t0, "embedding")
    if model_ok:
        ok(f"Model {svc.model} available")
    else:
        fail("Embedding model not available!")
        return {"status": "error"}

    # Step 2: Show what needs embedding
    section("Step 2: Content awaiting embedding")

    yt_rows = db_rows(conn, """
        SELECT yt.video_id, yt.title, yt.ticker, LENGTH(yt.raw_transcript) as len
        FROM youtube_transcripts yt
        LEFT JOIN (SELECT DISTINCT source_id FROM embeddings WHERE source_type='youtube') e
          ON yt.video_id = e.source_id
        WHERE yt.raw_transcript IS NOT NULL AND LENGTH(yt.raw_transcript) > 50 AND e.source_id IS NULL
    """)
    data("YouTube NOT embedded", f"{len(yt_rows)} transcripts")
    for r in yt_rows:
        info(f"  {r['video_id']}: {r['title']} ({r['len']} chars) ticker={r['ticker']}")

    reddit_rows = db_rows(conn, """
        SELECT dt.ticker, dt.source, LENGTH(dt.context_snippet) as len
        FROM discovered_tickers dt
        LEFT JOIN (SELECT DISTINCT source_id FROM embeddings WHERE source_type='reddit') e
          ON CAST(dt.rowid AS VARCHAR) = e.source_id
        WHERE dt.source LIKE '%reddit%' AND LENGTH(dt.context_snippet) > 30 AND e.source_id IS NULL
    """)
    data("Reddit NOT embedded", f"{len(reddit_rows)} posts")
    for r in reddit_rows:
        info(f"  ${r['ticker']}: {r['source']} ({r['len']} chars)")

    news_rows = db_rows(conn, """
        SELECT COUNT(*) as cnt FROM (
            SELECT nfa.article_hash FROM news_full_articles nfa
            LEFT JOIN (SELECT DISTINCT source_id FROM embeddings WHERE source_type='news') e
              ON nfa.article_hash = e.source_id
            WHERE LENGTH(nfa.content) > 50 AND e.source_id IS NULL
            UNION
            SELECT na.article_hash FROM news_articles na
            LEFT JOIN (SELECT DISTINCT source_id FROM embeddings WHERE source_type='news') e
              ON na.article_hash = e.source_id
            WHERE LENGTH(na.summary) > 50 AND e.source_id IS NULL
        )
    """)
    news_count = news_rows[0]["cnt"] if news_rows else 0
    data("News NOT embedded", f"{news_count} articles")

    # Step 3: Run embedding
    section("Step 3: Running embed_all_sources()")
    t0 = bench_start("embed_all_sources")
    try:
        result = await svc.embed_all_sources()
    except Exception as exc:
        fail(f"Embedding failed: {exc}")
        import traceback; traceback.print_exc()
        bench_end("embed_all_sources", t0, "embedding")
        return {"status": "error", "reason": str(exc)}
    bench_end("embed_all_sources", t0, "embedding")

    total_chunks = result.get("total_chunks", 0)
    total_embedded = result.get("total_embedded", 0)
    ok(f"Embedded {total_embedded} sources → {total_chunks} chunks")

    for source in ["youtube", "reddit", "news", "decisions"]:
        sr = result.get(source, {})
        info(f"  {source}: {sr.get('embedded', 0)} embedded, {sr.get('total_chunks', 0)} chunks, {sr.get('skipped', 0)} skipped")

    # Step 4: Verify embeddings in DB
    section("Step 4: Embeddings table after embedding")
    embed_count = table_count(conn, "embeddings")
    ok(f"embeddings: {embed_count} rows")

    if embed_count > 0:
        sample = db_rows(conn, """
            SELECT source_type, source_id, ticker,
                   LEFT(chunk_text, 80) as preview,
                   LENGTH(CAST(embedding AS VARCHAR)) as vec_len
            FROM embeddings LIMIT 10
        """)
        for r in sample:
            info(f"  {r['source_type']} | ${r['ticker']} | {r['preview']}… | vec={r['vec_len']}")

    return result


# ══════════════════════════════════════════════════════════════
# PHASE: DEEP ANALYSIS
# ══════════════════════════════════════════════════════════════
async def audit_analysis(conn) -> dict:
    header("DEEP ANALYSIS", "Quant scorecard + data distillation for AAPL")
    from app.services.deep_analysis_service import DeepAnalysisService
    svc = DeepAnalysisService()

    # Step 1: Show input data
    section("Step 1: Input data available for AAPL")
    input_tables = {
        "price_history": "SELECT COUNT(*) as cnt FROM price_history WHERE ticker='AAPL'",
        "technicals": "SELECT COUNT(*) as cnt FROM technicals WHERE ticker='AAPL'",
        "fundamentals": "SELECT COUNT(*) as cnt FROM fundamentals WHERE ticker='AAPL'",
        "financial_history": "SELECT COUNT(*) as cnt FROM financial_history WHERE ticker='AAPL'",
        "balance_sheet": "SELECT COUNT(*) as cnt FROM balance_sheet WHERE ticker='AAPL'",
        "cash_flows": "SELECT COUNT(*) as cnt FROM cash_flows WHERE ticker='AAPL'",
        "risk_metrics": "SELECT COUNT(*) as cnt FROM risk_metrics WHERE ticker='AAPL'",
        "news_articles": "SELECT COUNT(*) as cnt FROM news_articles WHERE ticker='AAPL'",
        "news_full_articles": "SELECT COUNT(*) as cnt FROM news_full_articles WHERE tickers_found LIKE '%AAPL%'",
        "youtube_transcripts": "SELECT COUNT(*) as cnt FROM youtube_transcripts WHERE ticker='AAPL'",
        "youtube_trading_data": "SELECT COUNT(*) as cnt FROM youtube_trading_data WHERE ticker='AAPL'",
        "sec_13f_holdings": "SELECT COUNT(*) as cnt FROM sec_13f_holdings WHERE ticker='AAPL'",
        "congressional_trades": "SELECT COUNT(*) as cnt FROM congressional_trades WHERE ticker='AAPL'",
        "discovered_tickers": "SELECT COUNT(*) as cnt FROM discovered_tickers WHERE ticker='AAPL'",
        "analyst_data": "SELECT COUNT(*) as cnt FROM analyst_data WHERE ticker='AAPL'",
        "insider_activity": "SELECT COUNT(*) as cnt FROM insider_activity WHERE ticker='AAPL'",
        "earnings_calendar": "SELECT COUNT(*) as cnt FROM earnings_calendar WHERE ticker='AAPL'",
    }
    for name, query in input_tables.items():
        rows = db_rows(conn, query)
        count = rows[0]["cnt"] if rows else 0
        (ok if count > 0 else warn)(f"{name}: {count} rows")

    # Step 2: Show latest price + technicals preview
    section("Step 2: Latest data samples")
    latest_price = db_rows(conn, """
        SELECT date, open, high, low, close, volume
        FROM price_history WHERE ticker='AAPL' ORDER BY date DESC LIMIT 3
    """)
    for p in latest_price:
        info(f"  Price {p['date']}: O={p['open']:.2f} H={p['high']:.2f} L={p['low']:.2f} C={p['close']:.2f} V={p['volume']}")

    latest_tech = db_rows(conn, """
        SELECT date, rsi, macd, sma_20, sma_50, atr, bb_upper, bb_lower
        FROM technicals WHERE ticker='AAPL' ORDER BY date DESC LIMIT 1
    """)
    for t in latest_tech:
        info(f"  Tech {t['date']}: RSI={t['rsi']} MACD={t['macd']} SMA20={t['sma_20']} ATR={t['atr']} BB=[{t['bb_lower']},{t['bb_upper']}]")

    # Step 3: Run analysis
    section("Step 3: Running DeepAnalysisService.analyze_ticker('AAPL')")
    portfolio_ctx = {
        "cash_balance": 100_000.0,
        "total_portfolio_value": 100_000.0,
        "positions": {},
        "realized_pnl": 0.0,
    }

    t0 = bench_start("analyze_ticker")
    try:
        dossier = await svc.analyze_ticker("AAPL", portfolio_context=portfolio_ctx, bot_id="default")
    except Exception as exc:
        fail(f"Analysis failed: {exc}")
        import traceback; traceback.print_exc()
        bench_end("analyze_ticker", t0, "analysis")
        return {"status": "error", "reason": str(exc)}
    bench_end("analyze_ticker", t0, "analysis")

    # Step 4: Inspect dossier output
    section("Step 4: Dossier Output")
    data("Ticker", dossier.ticker)
    data("Conviction Score", f"{dossier.conviction_score:.2f}")
    signal = "BUY" if dossier.conviction_score >= 0.7 else "SELL" if dossier.conviction_score <= 0.3 else "HOLD"
    data("Signal", signal)

    section("Step 4a: Quant Scorecard (Layer 1)")
    sc = dossier.quant_scorecard
    if sc:
        data("Trend Template", f"{sc.trend_template_score:.0f}/100")
        data("Relative Strength", f"{sc.relative_strength_rating:.0f}/100")
        data("Sharpe Ratio", f"{sc.sharpe_ratio:.2f}")
        data("Sortino Ratio", f"{sc.sortino_ratio:.2f}")
        data("Kelly Fraction", f"{sc.kelly_fraction:.2%}")
        data("VaR 95", f"{sc.var_95:.2%}")
        data("Max Drawdown", f"{sc.max_drawdown:.2%}")
        data("Z-Score 20d", f"{sc.z_score_20d:.2f}")
        data("Flags", sc.flags)
    else:
        fail("No quant scorecard produced!")

    section("Step 4b: Executive Summary (price analysis)")
    if dossier.executive_summary:
        ok(f"Length: {len(dossier.executive_summary)} chars")
        data("Preview", dossier.executive_summary[:500])
    else:
        fail("executive_summary: EMPTY")

    section("Step 4c: Bull Case (fundamentals)")
    if dossier.bull_case:
        ok(f"Length: {len(dossier.bull_case)} chars")
        data("Preview", dossier.bull_case[:500])
    else:
        fail("bull_case: EMPTY")

    section("Step 4d: Bear Case (risk)")
    if dossier.bear_case:
        ok(f"Length: {len(dossier.bear_case)} chars")
        data("Preview", dossier.bear_case[:500])
    else:
        fail("bear_case: EMPTY")

    # Step 5: Distillation fields (Layer 2)
    section("Step 5: Distillation Fields (Layer 2 — Pure Python)")
    distill_fields = {
        "news_analysis": "News distillation",
        "youtube_analysis": "YouTube distillation",
        "smart_money_analysis": "Smart money (SEC 13F + Congress)",
        "reddit_analysis": "Reddit sentiment",
        "peer_analysis": "Peer comparison",
        "analyst_consensus_analysis": "Analyst consensus",
        "insider_activity_analysis": "Insider activity",
        "earnings_catalyst_analysis": "Earnings catalyst",
        "cross_signal_summary": "Cross-signal synthesis",
    }
    for field, label in distill_fields.items():
        value = getattr(dossier, field, None)
        if value and len(str(value)) > 20:
            ok(f"{label}: {len(str(value))} chars")
            info(f"  Preview: {str(value)[:200]}")
        elif value:
            warn(f"{label}: only {len(str(value))} chars — suspiciously short")
            info(f"  Content: {value}")
        else:
            fail(f"{label}: EMPTY — distillation not producing output")

    # Step 6: Verify persisted to DB
    section("Step 6: Verify dossier persisted to DB")
    sc_count = table_count(conn, "quant_scorecards")
    td_count = table_count(conn, "ticker_dossiers")
    (ok if sc_count > 0 else fail)(f"quant_scorecards: {sc_count} rows")
    (ok if td_count > 0 else fail)(f"ticker_dossiers: {td_count} rows")

    if td_count > 0:
        latest = db_rows(conn, """
            SELECT ticker, conviction_score,
                   LENGTH(executive_summary) as summary_len,
                   LENGTH(cross_signal_summary) as cross_len
            FROM ticker_dossiers WHERE ticker='AAPL'
            ORDER BY generated_at DESC LIMIT 1
        """)
        for r in latest:
            data("DB conviction", r["conviction_score"])
            data("DB summary_len", r["summary_len"])
            data("DB cross_signal_len", r["cross_len"])

    return {
        "status": "ok",
        "conviction": dossier.conviction_score,
        "signal": signal,
        "summary_len": len(dossier.executive_summary or ""),
    }


# ══════════════════════════════════════════════════════════════
# PHASE: TRADING
# ══════════════════════════════════════════════════════════════
async def audit_trading(conn) -> dict:
    header("TRADING", f"LLM trade decision via TradingPipelineService (model={TEST_MODEL})")
    from app.services.paper_trader import PaperTrader
    from app.services.trading_pipeline_service import TradingPipelineService

    paper_trader = PaperTrader(bot_id="default")

    # Step 1: Portfolio state
    section("Step 1: Portfolio before trading")
    portfolio = paper_trader.get_portfolio()
    data("Cash", f"${portfolio['cash_balance']:,.2f}")
    data("Total Value", f"${portfolio['total_portfolio_value']:,.2f}")
    data("Positions", portfolio.get("positions", []))

    # Step 2: Prerequisites
    section("Step 2: Prerequisites check")

    dossier_count = table_count(conn, "ticker_dossiers")
    if dossier_count > 0:
        ok(f"ticker_dossiers: {dossier_count}")
        # Show the dossier that will feed the LLM
        dossier = db_rows(conn, """
            SELECT conviction_score,
                   LENGTH(executive_summary) as exec_len,
                   LENGTH(cross_signal_summary) as cross_len,
                   generated_at
            FROM ticker_dossiers WHERE ticker='AAPL'
            ORDER BY generated_at DESC LIMIT 1
        """)
        if dossier:
            d = dossier[0]
            data("Dossier conviction", d["conviction_score"])
            data("Executive summary size", f"{d['exec_len']} chars")
            data("Cross-signal size", f"{d['cross_len'] or 0} chars")
    else:
        fail("No dossier for AAPL! Run analysis phase first.")
        return {"status": "error", "reason": "no_dossier"}

    tech_count = table_count(conn, "technicals")
    (ok if tech_count > 0 else warn)(f"technicals: {tech_count} rows")

    embed_count = table_count(conn, "embeddings")
    (ok if embed_count > 0 else warn)(f"embeddings: {embed_count} rows")

    # Step 3: Show what _build_context will read
    section("Step 3: Context data that feeds the LLM")

    # Latest technicals for ATR
    atr_row = db_rows(conn, "SELECT atr FROM technicals WHERE ticker='AAPL' ORDER BY date DESC LIMIT 1")
    data("ATR (from DB)", atr_row[0]["atr"] if atr_row else "missing")

    # Last price from DB (NOT live yfinance)
    price_row = db_rows(conn, "SELECT close, date FROM price_history WHERE ticker='AAPL' ORDER BY date DESC LIMIT 1")
    if price_row:
        data("Last price (from DB)", f"${price_row[0]['close']:.2f} on {price_row[0]['date']}")
    else:
        warn("No price data in DB!")

    # Previous trade decision (for delta analysis)
    prev_decision = db_rows(conn, """
        SELECT action, confidence, ts, LEFT(rationale, 150) as rationale
        FROM trade_decisions WHERE symbol='AAPL'
        ORDER BY ts DESC LIMIT 1
    """)
    if prev_decision:
        pd = prev_decision[0]
        data("Previous decision", f"{pd['action']} (conf={pd['confidence']}) on {str(pd['ts'])[:10]}")
        data("Previous rationale", pd["rationale"])
    else:
        info("No previous decision (first run)")

    # YouTube trading data
    yt_trades = db_rows(conn, """
        SELECT title, channel, LEFT(trading_data, 200) as data_preview
        FROM youtube_trading_data WHERE ticker='AAPL'
        ORDER BY collected_at DESC LIMIT 3
    """)
    data("YouTube trade signals", f"{len(yt_trades)} entries")
    for yt in yt_trades:
        info(f"  {yt['channel']}: {yt['title']}")
        info(f"    Data: {yt['data_preview']}")

    # RAG context
    rag_rows = db_rows(conn, """
        SELECT source_type, COUNT(*) as cnt, SUM(LENGTH(chunk_text)) as total_chars
        FROM embeddings WHERE ticker='AAPL'
        GROUP BY source_type
    """)
    data("RAG chunks available", f"{sum(r['cnt'] for r in rag_rows)} total")
    for r in rag_rows:
        info(f"  {r['source_type']}: {r['cnt']} chunks, {r['total_chars']} chars")

    # Step 4: Run trading pipeline
    section(f"Step 4: Running TradingPipelineService (model={TEST_MODEL}, dry_run=True)")

    pipeline = TradingPipelineService(
        paper_trader=paper_trader,
        dry_run=True,
        bot_id="default",
    )

    t0 = bench_start("trading_pipeline.run_once")
    try:
        result = await pipeline.run_once(["AAPL"])
    except Exception as exc:
        fail(f"Trading pipeline failed: {exc}")
        import traceback; traceback.print_exc()
        bench_end("trading_pipeline.run_once", t0, "trading")
        return {"status": "error", "reason": str(exc)}
    bench_end("trading_pipeline.run_once", t0, "trading")

    # Step 5: Show results
    section("Step 5: Trading Results")
    data("Decisions", result.get("decisions", 0))
    data("Orders", result.get("orders", 0))
    data("Filtered", result.get("filtered", 0))

    for tr in result.get("tickers", []):
        ticker = tr.get("ticker", "?")
        action = tr.get("action", "?")
        confidence = tr.get("confidence", 0)
        rationale = tr.get("rationale", "")
        risk_level = tr.get("risk_level", "?")
        exec_status = tr.get("exec_status", "?")

        section(f"  ${ticker} DECISION DETAIL")
        data("  Action", action)
        data("  Confidence", f"{confidence:.0%}" if isinstance(confidence, float) else confidence)
        data("  Risk Level", risk_level)
        data("  Exec Status", exec_status)
        data("  Rationale", rationale[:400] if rationale else "(empty)")

        if tr.get("error"):
            fail(f"  Error: {tr['error']}")

    # Step 6: Verify persisted to DB
    section("Step 6: Verify decision persisted to DB")
    td_rows = db_rows(conn, """
        SELECT action, confidence, risk_level, status,
               LEFT(rationale, 200) as rationale, ts
        FROM trade_decisions WHERE symbol='AAPL'
        ORDER BY ts DESC LIMIT 2
    """)
    data("trade_decisions for AAPL", f"{len(td_rows)} rows")
    for td in td_rows:
        info(f"  {td['ts']}: {td['action']} conf={td['confidence']} risk={td['risk_level']} status={td['status']}")
        info(f"    Rationale: {td['rationale']}")

    return result


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
async def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline Audit Runner")
    parser.add_argument(
        "--phase",
        choices=["db", "embedding", "analysis", "trading", "all"],
        default="all",
    )
    args = parser.parse_args()

    print(f"{BOLD}{CYAN}")
    print("╔════════════════════════════════════════════════════════════════╗")
    print("║       LAZY TRADING BOT — PIPELINE AUDIT (VERBOSE MODE)      ║")
    print("║       Prints EVERY step. Missing nothing.                    ║")
    print("╚════════════════════════════════════════════════════════════════╝")
    print(f"{RESET}")

    data("DB Profile", settings.DB_PROFILE)
    data("DB Path", settings.DB_PATH)
    data("LLM Model (forced)", TEST_MODEL)
    data("Ollama URL", settings.OLLAMA_URL)
    data("Prism URL", settings.PRISM_URL)
    data("Time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # ── Step 0: Unload ALL Ollama models for clean VRAM ──
    section("Step 0: Unload ALL Ollama models (clean slate)")
    import httpx
    ollama_url = settings.OLLAMA_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            ps = await client.get(f"{ollama_url}/api/ps")
            loaded = [m["name"] for m in ps.json().get("models", [])]
            if loaded:
                warn(f"Models in VRAM: {loaded}")
                for model_name in loaded:
                    await client.post(
                        f"{ollama_url}/api/generate",
                        json={"model": model_name, "keep_alive": "0"},
                    )
                    ok(f"Unloaded: {model_name}")
            else:
                ok("VRAM clean — no models loaded")
    except Exception as exc:
        fail(f"Could not unload models: {exc}")

    import duckdb
    conn = duckdb.connect(str(settings.DB_PATH), read_only=False)

    results = {}
    t_total = time.perf_counter()

    # Always show DB status
    audit_db_status(conn)

    if args.phase in ("embedding", "all"):
        results["embedding"] = await audit_embedding(conn)

    if args.phase in ("analysis", "all"):
        results["analysis"] = await audit_analysis(conn)

    if args.phase in ("trading", "all"):
        results["trading"] = await audit_trading(conn)

    # ── Benchmark Summary ──
    total_elapsed = time.perf_counter() - t_total
    print(f"\n{BOLD}{CYAN}{'═' * 72}")
    print(f"  AUDIT COMPLETE — {total_elapsed:.1f}s total")
    print(f"{'═' * 72}{RESET}")

    if BENCHMARKS:
        section("⏱  Benchmark Summary")
        # Sort by duration desc
        for b in sorted(BENCHMARKS, key=lambda x: -x["ms"]):
            bar_len = min(int(b["ms"] / 100), 40)
            bar = "█" * bar_len
            if b["ms"] > 5000:
                color = RED
            elif b["ms"] > 1000:
                color = YELLOW
            else:
                color = GREEN
            print(f"  {color}{b['ms']:>8.1f}ms{RESET}  {bar}  {b['step']}")

    section("Phase Results")
    for phase, result in results.items():
        status = result.get("status", "ok") if isinstance(result, dict) else "ok"
        icon = "✅" if status != "error" else "❌"
        print(f"  {icon} {phase}")

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
