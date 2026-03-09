"""Research Tools — optional deep-research tools the LLM can call.

These tools give the trading agent flexibility to dig deeper into
specific areas before making a BUY/SELL/HOLD decision. Each tool queries
existing data sources (DuckDB tables, yfinance, existing services) and
returns structured dicts the LLM can reason about.

The TOOL_REGISTRY and TOOL_DESCRIPTIONS constants are imported by
TradingAgent and PortfolioStrategist to expose these tools to the LLM.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine

from app.database import get_db
from app.utils.logger import logger


# ------------------------------------------------------------------
# Tool implementations
# ------------------------------------------------------------------

async def fetch_sec_filings(params: dict) -> dict:
  """Fetch recent SEC 13F institutional holdings for a ticker.

  Shows which big funds (hedge funds, mutual funds) are buying/selling.
  """
  ticker = str(params.get("ticker", "")).upper().strip()
  if not ticker:
    return {"error": "Missing 'ticker' parameter"}

  db = get_db()
  try:
    rows = db.execute(
      """
      SELECT f.filer_name, h.shares, h.value_usd,
             h.filing_quarter, h.filing_date
      FROM sec_13f_holdings h
      JOIN sec_13f_filers f ON h.cik = f.cik
      WHERE h.ticker = ?
      ORDER BY h.filing_date DESC
      LIMIT 15
      """,
      [ticker],
    ).fetchall()

    if not rows:
      return {"ticker": ticker, "holdings": [], "note": "No 13F data found"}

    cols = ["filer_name", "shares", "value_usd", "filing_quarter", "filing_date"]
    holdings = [dict(zip(cols, r)) for r in rows]

    # Serialize dates
    for h in holdings:
      if h.get("filing_date"):
        h["filing_date"] = str(h["filing_date"])

    return {
      "ticker": ticker,
      "holdings_count": len(holdings),
      "holdings": holdings,
    }
  except Exception as exc:
    logger.warning("[ResearchTools] fetch_sec_filings failed for %s: %s", ticker, exc)
    return {"ticker": ticker, "error": str(exc)}


async def search_news(params: dict) -> dict:
  """Search for recent news articles about a ticker or topic.

  Returns headlines, publishers, and summaries from collected news.
  """
  ticker = str(params.get("ticker", "")).upper().strip()
  query = str(params.get("query", "")).strip()
  limit = min(int(params.get("limit", 10)), 20)

  if not ticker and not query:
    return {"error": "Provide 'ticker' or 'query' parameter"}

  db = get_db()
  try:
    if ticker:
      rows = db.execute(
        """
        SELECT title, publisher, published_at, summary, source
        FROM news_articles
        WHERE ticker = ?
        ORDER BY published_at DESC
        LIMIT ?
        """,
        [ticker, limit],
      ).fetchall()
    else:
      rows = db.execute(
        """
        SELECT title, publisher, published_at, summary, source
        FROM news_articles
        WHERE LOWER(title) LIKE LOWER(?) OR LOWER(summary) LIKE LOWER(?)
        ORDER BY published_at DESC
        LIMIT ?
        """,
        [f"%{query}%", f"%{query}%", limit],
      ).fetchall()

    if not rows:
      # Try RSS full articles as fallback
      rows = db.execute(
        """
        SELECT title, publisher, published_at, summary, source_feed AS source
        FROM news_full_articles
        WHERE LOWER(title) LIKE LOWER(?)
           OR LOWER(tickers_found) LIKE LOWER(?)
        ORDER BY published_at DESC
        LIMIT ?
        """,
        [f"%{ticker or query}%", f"%{ticker or query}%", limit],
      ).fetchall()

    cols = ["title", "publisher", "published_at", "summary", "source"]
    articles = [dict(zip(cols, r)) for r in rows]

    for a in articles:
      if a.get("published_at"):
        a["published_at"] = str(a["published_at"])
      # Truncate summaries for context budget
      if a.get("summary") and len(a["summary"]) > 200:
        a["summary"] = a["summary"][:200] + "…"

    return {
      "ticker": ticker or query,
      "articles_count": len(articles),
      "articles": articles,
    }
  except Exception as exc:
    logger.warning("[ResearchTools] search_news failed: %s", exc)
    return {"error": str(exc)}


async def get_technicals_detail(params: dict) -> dict:
  """Get detailed technical indicators for a ticker.

  Returns RSI, MACD, Bollinger Bands, ADX, Stochastic, Ichimoku,
  moving averages, volume indicators, and more.
  """
  ticker = str(params.get("ticker", "")).upper().strip()
  if not ticker:
    return {"error": "Missing 'ticker' parameter"}

  db = get_db()
  try:
    row = db.execute(
      """
      SELECT date, rsi, macd, macd_signal, macd_hist,
             sma_20, sma_50, sma_200, ema_9, ema_21,
             bb_upper, bb_middle, bb_lower, atr, natr,
             stoch_k, stoch_d, adx, adx_dmp, adx_dmn,
             cci, willr, mfi, roc, obv, cmf,
             aroon_up, aroon_down, aroon_osc,
             supertrend, psar, chop,
             ichi_conv, ichi_base, ichi_span_a, ichi_span_b,
             fib_236, fib_382, fib_500, fib_618,
             zscore, skew, kurtosis
      FROM technicals
      WHERE ticker = ?
      ORDER BY date DESC
      LIMIT 1
      """,
      [ticker],
    ).fetchone()

    if not row:
      return {"ticker": ticker, "error": "No technical data found"}

    cols = [
      "date", "rsi", "macd", "macd_signal", "macd_hist",
      "sma_20", "sma_50", "sma_200", "ema_9", "ema_21",
      "bb_upper", "bb_middle", "bb_lower", "atr", "natr",
      "stoch_k", "stoch_d", "adx", "adx_dmp", "adx_dmn",
      "cci", "willr", "mfi", "roc", "obv", "cmf",
      "aroon_up", "aroon_down", "aroon_osc",
      "supertrend", "psar", "chop",
      "ichi_conv", "ichi_base", "ichi_span_a", "ichi_span_b",
      "fib_236", "fib_382", "fib_500", "fib_618",
      "zscore", "skew", "kurtosis",
    ]
    data = dict(zip(cols, row))
    data["date"] = str(data["date"])

    # Round floats for readability
    for k, v in data.items():
      if isinstance(v, float):
        data[k] = round(v, 4)

    # Add interpretation hints
    hints = []
    rsi = data.get("rsi")
    if rsi and rsi > 70:
      hints.append("RSI overbought (>70)")
    elif rsi and rsi < 30:
      hints.append("RSI oversold (<30)")

    adx = data.get("adx")
    if adx and adx > 25:
      hints.append(f"Strong trend (ADX={adx:.0f})")
    elif adx and adx < 20:
      hints.append(f"Weak trend (ADX={adx:.0f})")

    macd_hist = data.get("macd_hist")
    if macd_hist and macd_hist > 0:
      hints.append("MACD bullish (histogram > 0)")
    elif macd_hist and macd_hist < 0:
      hints.append("MACD bearish (histogram < 0)")

    data["interpretation_hints"] = hints

    return {"ticker": ticker, "technicals": data}
  except Exception as exc:
    logger.warning("[ResearchTools] get_technicals_detail failed for %s: %s", ticker, exc)
    return {"ticker": ticker, "error": str(exc)}


async def check_insider_activity(params: dict) -> dict:
  """Check recent insider buying/selling and congressional trades.

  Combines insider_activity table + congressional_trades for a
  complete smart-money picture.
  """
  ticker = str(params.get("ticker", "")).upper().strip()
  if not ticker:
    return {"error": "Missing 'ticker' parameter"}

  db = get_db()
  result: dict[str, Any] = {"ticker": ticker}

  # Insider activity
  try:
    row = db.execute(
      """
      SELECT snapshot_date, net_insider_buying_90d,
             institutional_ownership_pct, raw_transactions
      FROM insider_activity
      WHERE ticker = ?
      ORDER BY snapshot_date DESC
      LIMIT 1
      """,
      [ticker],
    ).fetchone()

    if row:
      result["insider"] = {
        "snapshot_date": str(row[0]),
        "net_insider_buying_90d": row[1],
        "institutional_ownership_pct": row[2],
      }
      # Parse raw transactions if available
      if row[3]:
        try:
          txns = json.loads(row[3])
          if isinstance(txns, list):
            result["insider"]["recent_transactions"] = txns[:5]
        except (json.JSONDecodeError, TypeError):
          pass
    else:
      result["insider"] = {"note": "No insider data found"}
  except Exception as exc:
    result["insider"] = {"error": str(exc)}

  # Congressional trades
  try:
    rows = db.execute(
      """
      SELECT member_name, chamber, tx_type, tx_date,
             filed_date, amount_range
      FROM congressional_trades
      WHERE ticker = ?
      ORDER BY tx_date DESC
      LIMIT 10
      """,
      [ticker],
    ).fetchall()

    if rows:
      cols = ["member", "chamber", "type", "tx_date", "filed_date", "amount"]
      trades = [dict(zip(cols, r)) for r in rows]
      for t in trades:
        if t.get("tx_date"):
          t["tx_date"] = str(t["tx_date"])
        if t.get("filed_date"):
          t["filed_date"] = str(t["filed_date"])
      result["congress_trades"] = trades
      result["congress_count"] = len(trades)
    else:
      result["congress_trades"] = []
      result["congress_count"] = 0
  except Exception as exc:
    result["congress_trades_error"] = str(exc)

  return result


async def compare_financials(params: dict) -> dict:
  """Compare fundamentals of 2-3 tickers side by side.

  Returns key financial metrics for comparison: P/E, revenue,
  margins, debt, growth rates, etc.
  """
  tickers_raw = params.get("tickers", [])
  if isinstance(tickers_raw, str):
    tickers_raw = [t.strip() for t in tickers_raw.split(",")]
  tickers = [t.upper().strip() for t in tickers_raw if t.strip()]

  if len(tickers) < 2:
    return {"error": "Provide at least 2 tickers in 'tickers' array"}
  tickers = tickers[:4]  # Cap at 4

  db = get_db()
  comparisons = []

  for ticker in tickers:
    try:
      row = db.execute(
        """
        SELECT ticker, market_cap, trailing_pe, forward_pe,
               peg_ratio, price_to_sales, price_to_book,
               profit_margin, operating_margin,
               return_on_equity, return_on_assets,
               revenue, revenue_growth, net_income,
               total_cash, total_debt, debt_to_equity,
               free_cash_flow, dividend_yield,
               sector, industry
        FROM fundamentals
        WHERE ticker = ?
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        [ticker],
      ).fetchone()

      if row:
        cols = [
          "ticker", "market_cap", "trailing_pe", "forward_pe",
          "peg_ratio", "price_to_sales", "price_to_book",
          "profit_margin", "operating_margin",
          "return_on_equity", "return_on_assets",
          "revenue", "revenue_growth", "net_income",
          "total_cash", "total_debt", "debt_to_equity",
          "free_cash_flow", "dividend_yield",
          "sector", "industry",
        ]
        data = dict(zip(cols, row))
        # Round for readability
        for k, v in data.items():
          if isinstance(v, float):
            data[k] = round(v, 4)
        comparisons.append(data)
      else:
        comparisons.append({"ticker": ticker, "error": "No fundamental data"})
    except Exception as exc:
      comparisons.append({"ticker": ticker, "error": str(exc)})

  return {
    "comparison_count": len(comparisons),
    "tickers": comparisons,
    "note": "Compare P/E, margins, growth rates, and debt levels across tickers.",
  }


async def get_price_history(params: dict) -> dict:
  """Get recent price action for a ticker.

  Returns OHLCV data for the last N trading days (default 20).
  Useful for spotting recent patterns, gaps, or support/resistance.
  """
  ticker = str(params.get("ticker", "")).upper().strip()
  period = str(params.get("period", "20d")).lower()

  if not ticker:
    return {"error": "Missing 'ticker' parameter"}

  # Parse period into number of rows
  if period.endswith("d"):
    limit = min(int(period[:-1]), 60)
  elif period.endswith("mo"):
    limit = min(int(period[:-2]) * 21, 63)
  else:
    limit = 20

  db = get_db()
  try:
    rows = db.execute(
      """
      SELECT date, open, high, low, close, volume
      FROM price_history
      WHERE ticker = ?
      ORDER BY date DESC
      LIMIT ?
      """,
      [ticker, limit],
    ).fetchall()

    if not rows:
      return {"ticker": ticker, "error": "No price history found"}

    cols = ["date", "open", "high", "low", "close", "volume"]
    prices = [dict(zip(cols, r)) for r in reversed(rows)]  # Chronological

    for p in prices:
      p["date"] = str(p["date"])
      for k in ["open", "high", "low", "close"]:
        if isinstance(p[k], float):
          p[k] = round(p[k], 2)

    # Summary stats
    closes = [p["close"] for p in prices if p["close"]]
    if closes:
      high_price = max(closes)
      low_price = min(closes)
      change_pct = ((closes[-1] / closes[0]) - 1) * 100 if closes[0] else 0

      return {
        "ticker": ticker,
        "period": period,
        "data_points": len(prices),
        "summary": {
          "period_high": round(high_price, 2),
          "period_low": round(low_price, 2),
          "period_change_pct": round(change_pct, 2),
          "latest_close": round(closes[-1], 2),
          "avg_volume": round(
            sum(p["volume"] for p in prices if p["volume"]) / len(prices)
          ),
        },
        "prices": prices,
      }
    return {"ticker": ticker, "prices": prices}
  except Exception as exc:
    logger.warning("[ResearchTools] get_price_history failed for %s: %s", ticker, exc)
    return {"ticker": ticker, "error": str(exc)}


async def search_reddit_sentiment(params: dict) -> dict:
  """Search for recent Reddit mentions and sentiment for a ticker.

  Pulls from the discovered_tickers table and ticker_scores for
  Reddit-sourced sentiment data.
  """
  ticker = str(params.get("ticker", "")).upper().strip()
  if not ticker:
    return {"error": "Missing 'ticker' parameter"}

  db = get_db()
  result: dict[str, Any] = {"ticker": ticker}

  # Ticker scores (aggregated)
  try:
    row = db.execute(
      """
      SELECT total_score, reddit_score, youtube_score,
             mention_count, sentiment_hint, first_seen, last_seen
      FROM ticker_scores
      WHERE ticker = ?
      """,
      [ticker],
    ).fetchone()

    if row:
      result["scores"] = {
        "total_score": row[0],
        "reddit_score": row[1],
        "youtube_score": row[2],
        "mention_count": row[3],
        "sentiment_hint": row[4],
        "first_seen": str(row[5]) if row[5] else None,
        "last_seen": str(row[6]) if row[6] else None,
      }
  except Exception as exc:
    result["scores_error"] = str(exc)

  # Recent discovery mentions (Reddit-specific)
  try:
    rows = db.execute(
      """
      SELECT source, source_detail, discovery_score,
             sentiment_hint, context_snippet, discovered_at
      FROM discovered_tickers
      WHERE ticker = ? AND source = 'reddit'
      ORDER BY discovered_at DESC
      LIMIT 10
      """,
      [ticker],
    ).fetchall()

    if rows:
      cols = [
        "source", "source_detail", "score",
        "sentiment", "context", "discovered_at",
      ]
      mentions = [dict(zip(cols, r)) for r in rows]
      for m in mentions:
        m["discovered_at"] = str(m["discovered_at"]) if m["discovered_at"] else None
        # Truncate context
        if m.get("context") and len(m["context"]) > 150:
          m["context"] = m["context"][:150] + "…"
      result["reddit_mentions"] = mentions
      result["reddit_mention_count"] = len(mentions)
    else:
      result["reddit_mentions"] = []
      result["reddit_mention_count"] = 0
  except Exception as exc:
    result["mentions_error"] = str(exc)

  return result


async def get_earnings_calendar(params: dict) -> dict:
  """Get upcoming earnings date and recent earnings surprises.

  Critical for avoiding earnings traps or timing entries.
  """
  ticker = str(params.get("ticker", "")).upper().strip()
  if not ticker:
    return {"error": "Missing 'ticker' parameter"}

  db = get_db()
  result: dict[str, Any] = {"ticker": ticker}

  # From DB
  try:
    row = db.execute(
      """
      SELECT snapshot_date, next_earnings_date, days_until_earnings,
             earnings_estimate, previous_actual, previous_estimate,
             surprise_pct
      FROM earnings_calendar
      WHERE ticker = ?
      ORDER BY snapshot_date DESC
      LIMIT 1
      """,
      [ticker],
    ).fetchone()

    if row:
      result["earnings"] = {
        "snapshot_date": str(row[0]),
        "next_earnings_date": str(row[1]) if row[1] else "Unknown",
        "days_until_earnings": row[2],
        "earnings_estimate": row[3],
        "previous_actual": row[4],
        "previous_estimate": row[5],
        "surprise_pct": round(row[6], 2) if row[6] else None,
      }

      # Add warning if earnings are imminent
      if row[2] and row[2] <= 7:
        result["warning"] = (
          f"⚠️ Earnings in {row[2]} days! "
          f"High volatility expected. Consider waiting."
        )
    else:
      result["earnings"] = {"note": "No earnings calendar data found"}
  except Exception as exc:
    result["earnings"] = {"error": str(exc)}

  # Also fetch analyst consensus
  try:
    row = db.execute(
      """
      SELECT target_mean, target_median, target_high, target_low,
             num_analysts, strong_buy, buy, hold, sell, strong_sell
      FROM analyst_data
      WHERE ticker = ?
      ORDER BY snapshot_date DESC
      LIMIT 1
      """,
      [ticker],
    ).fetchone()

    if row:
      result["analyst_consensus"] = {
        "target_mean": round(row[0], 2) if row[0] else None,
        "target_median": round(row[1], 2) if row[1] else None,
        "target_high": round(row[2], 2) if row[2] else None,
        "target_low": round(row[3], 2) if row[3] else None,
        "num_analysts": row[4],
        "ratings": {
          "strong_buy": row[5],
          "buy": row[6],
          "hold": row[7],
          "sell": row[8],
          "strong_sell": row[9],
        },
      }
  except Exception as exc:
    result["analyst_error"] = str(exc)

  return result


# ------------------------------------------------------------------
# Tool Category Index — used by the search_tools meta-tool
# ------------------------------------------------------------------

TOOL_CATEGORIES: dict[str, list[str]] = {
  "technicals": ["get_technicals_detail", "get_price_history"],
  "fundamentals": ["compare_financials", "fetch_sec_filings"],
  "sentiment": ["search_news", "search_reddit_sentiment"],
  "insider": ["check_insider_activity"],
  "earnings": ["get_earnings_calendar"],
  "news": ["search_news"],
  "institutional": ["fetch_sec_filings", "check_insider_activity"],
  "price": ["get_price_history", "get_technicals_detail"],
  "filings": ["fetch_sec_filings"],
  "congress": ["check_insider_activity"],
  "reddit": ["search_reddit_sentiment"],
  "analysis": ["get_technicals_detail", "compare_financials"],
  "valuation": ["compare_financials"],
  "risk": ["get_technicals_detail", "get_price_history", "check_insider_activity"],
}

# Per-tool detail descriptions — returned by search_tools
TOOL_DETAIL_DESCRIPTIONS: dict[str, str] = {
  "fetch_sec_filings": (
    "fetch_sec_filings — See which hedge funds / institutions are "
    "buying/selling this stock.\n"
    "Params: ticker (str)\n"
    'Example: {"tool": "fetch_sec_filings", "params": {"ticker": "NVDA"}}'
  ),
  "search_news": (
    "search_news — Recent news headlines and summaries.\n"
    "Params: ticker (str), query (str, optional), limit (int, max 20)\n"
    'Example: {"tool": "search_news", "params": {"ticker": "AAPL"}}'
  ),
  "get_technicals_detail": (
    "get_technicals_detail — Full technical breakdown: RSI, MACD, "
    "Bollinger, ADX, Ichimoku, Fibonacci, volume indicators.\n"
    "Params: ticker (str)\n"
    'Example: {"tool": "get_technicals_detail", "params": {"ticker": "TSLA"}}'
  ),
  "check_insider_activity": (
    "check_insider_activity — Insider buying/selling + congressional "
    "trading activity.\n"
    "Params: ticker (str)\n"
    'Example: {"tool": "check_insider_activity", "params": {"ticker": "MSFT"}}'
  ),
  "compare_financials": (
    "compare_financials — Side-by-side P/E, margins, growth, debt for "
    "2-4 tickers.\n"
    "Params: tickers (list of str)\n"
    'Example: {"tool": "compare_financials", "params": {"tickers": ["NVDA", "AMD"]}}'
  ),
  "get_price_history": (
    "get_price_history — Recent OHLCV data with summary stats.\n"
    "Params: ticker (str), period (str, default '20d')\n"
    'Example: {"tool": "get_price_history", "params": {"ticker": "GOOGL", "period": "20d"}}'
  ),
  "search_reddit_sentiment": (
    "search_reddit_sentiment — Reddit mentions, sentiment, and discovery "
    "scores.\n"
    "Params: ticker (str)\n"
    'Example: {"tool": "search_reddit_sentiment", "params": {"ticker": "PLTR"}}'
  ),
  "get_earnings_calendar": (
    "get_earnings_calendar — Upcoming earnings date, analyst estimates, "
    "and surprises.\n"
    "Params: ticker (str)\n"
    'Example: {"tool": "get_earnings_calendar", "params": {"ticker": "META"}}'
  ),
}


# ------------------------------------------------------------------
# search_tools — meta-tool the LLM calls to discover available tools
# ------------------------------------------------------------------

async def search_tools(params: dict) -> dict:
  """Search for available research tools by category or keyword.

  Returns matching tool names + descriptions so the LLM can decide
  which one to call next.
  """
  query = str(params.get("query", "")).lower().strip()
  if not query:
    return {
      "error": "Provide a 'query' string (e.g. 'insider', 'technicals', 'news')",
      "available_categories": sorted(TOOL_CATEGORIES.keys()),
    }

  matched_tools: set[str] = set()

  # Direct category match
  if query in TOOL_CATEGORIES:
    matched_tools.update(TOOL_CATEGORIES[query])

  # Fuzzy: check if query appears in any category name or tool name
  for cat, tools in TOOL_CATEGORIES.items():
    if query in cat:
      matched_tools.update(tools)
  for tool_name in TOOL_DETAIL_DESCRIPTIONS:
    if query in tool_name:
      matched_tools.add(tool_name)

  # Also search within descriptions
  for tool_name, desc in TOOL_DETAIL_DESCRIPTIONS.items():
    if query in desc.lower():
      matched_tools.add(tool_name)

  if not matched_tools:
    return {
      "query": query,
      "matched_tools": [],
      "note": "No tools found. Try: " + ", ".join(sorted(TOOL_CATEGORIES.keys())),
    }

  # Return descriptions of matched tools
  tool_results = []
  for name in sorted(matched_tools):
    if name in TOOL_DETAIL_DESCRIPTIONS:
      tool_results.append({
        "tool_name": name,
        "description": TOOL_DETAIL_DESCRIPTIONS[name],
      })

  return {
    "query": query,
    "matched_count": len(tool_results),
    "tools": tool_results,
  }


# ------------------------------------------------------------------
# Memory tools — save_finding / recall_findings
# ------------------------------------------------------------------

async def save_finding(params: dict) -> dict:
  """Save a key finding to the scratchpad.

  Findings persist even when older tool results are trimmed
  from context. Use this to record important data points.
  """
  note = str(params.get("note", "")).strip()
  if not note:
    return {"error": "Provide a 'note' string with your finding"}
  # The actual persistence is managed by the caller (TradingAgent)
  # via the _findings list. This tool just validates and returns.
  return {"status": "saved", "note": note}


async def recall_findings(params: dict) -> dict:
  """Recall all saved findings for the current analysis.

  Returns all notes saved via save_finding during this session.
  """
  # Results are injected by the TradingAgent caller, not stored here.
  return {"status": "recalled", "note": "Findings will be injected by the agent."}


# ------------------------------------------------------------------
# Tool Registry — used by TradingAgent and PortfolioStrategist
# ------------------------------------------------------------------

ToolFunc = Callable[[dict], Coroutine[Any, Any, dict]]

TOOL_REGISTRY: dict[str, ToolFunc] = {
  "fetch_sec_filings": fetch_sec_filings,
  "search_news": search_news,
  "get_technicals_detail": get_technicals_detail,
  "check_insider_activity": check_insider_activity,
  "compare_financials": compare_financials,
  "get_price_history": get_price_history,
  "search_reddit_sentiment": search_reddit_sentiment,
  "get_earnings_calendar": get_earnings_calendar,
  # Meta-tools
  "search_tools": search_tools,
  "save_finding": save_finding,
  "recall_findings": recall_findings,
}

RESEARCH_TOOL_NAMES = list(TOOL_REGISTRY.keys())

# Compact meta-tool description (~100 tokens vs ~800 for full list)
SEARCH_TOOL_DESCRIPTION = """\
### RESEARCH TOOLS (on-demand — search for tools you need)

You have access to research tools (technical analysis, news, insider
activity, SEC filings, earnings, Reddit sentiment, and more). To
discover what's available, call:

### search_tools
Find research tools by category or keyword.
Params: query (str — e.g. "technicals", "insider", "news", "earnings")
Example: {"tool": "search_tools", "params": {"query": "insider"}}
→ Returns matching tool names + descriptions you can then call.

### save_finding
Save an important data point to your scratchpad (persists across context trimming).
Params: note (str)
Example: {"tool": "save_finding", "params": {"note": "RSI=28, oversold condition"}}

### recall_findings
Recall all saved findings for the current analysis.
Example: {"tool": "recall_findings", "params": {}}
"""

# Legacy full descriptions (kept for backward compatibility / PortfolioStrategist)
RESEARCH_TOOL_DESCRIPTIONS = """\
### RESEARCH TOOLS (optional — use these to investigate before deciding)

You may call these tools to gather more data before making your trading
decision. If you already have enough information, skip them and output
your decision directly.

### fetch_sec_filings
See which hedge funds and institutions are buying/selling this stock.
Params: ticker (str)
Example: {"tool": "fetch_sec_filings", "params": {"ticker": "NVDA"}}

### search_news
Search for recent news about a ticker or topic.
Params: ticker (str), query (str, optional), limit (int, optional, max 20)
Example: {"tool": "search_news", "params": {"ticker": "AAPL"}}

### get_technicals_detail
Full technical indicator breakdown: RSI, MACD, Bollinger, ADX, Ichimoku,
Fibonacci levels, volume indicators, and more.
Params: ticker (str)
Example: {"tool": "get_technicals_detail", "params": {"ticker": "TSLA"}}

### check_insider_activity
Recent insider buying/selling + congressional trading activity.
Params: ticker (str)
Example: {"tool": "check_insider_activity", "params": {"ticker": "MSFT"}}

### compare_financials
Side-by-side financial comparison of 2-4 tickers (P/E, margins, growth, debt).
Params: tickers (list of str)
Example: {"tool": "compare_financials", "params": {"tickers": ["NVDA", "AMD", "INTC"]}}

### get_price_history
Recent OHLCV price data with summary stats.
Params: ticker (str), period (str — "5d", "20d", "60d", default "20d")
Example: {"tool": "get_price_history", "params": {"ticker": "GOOGL", "period": "20d"}}

### search_reddit_sentiment
Reddit mentions, sentiment, and discovery scores for a ticker.
Params: ticker (str)
Example: {"tool": "search_reddit_sentiment", "params": {"ticker": "PLTR"}}

### get_earnings_calendar
Upcoming earnings date, analyst estimates, and recent surprises.
Params: ticker (str)
Example: {"tool": "get_earnings_calendar", "params": {"ticker": "META"}}

### search_tools
Find additional research tools by category or keyword.
Params: query (str)
Example: {"tool": "search_tools", "params": {"query": "insider"}}

### save_finding
Save a key finding to your scratchpad.
Params: note (str)
Example: {"tool": "save_finding", "params": {"note": "NVDA insider buying up 300%"}}

### recall_findings
Recall all saved findings.
Example: {"tool": "recall_findings", "params": {}}
"""

