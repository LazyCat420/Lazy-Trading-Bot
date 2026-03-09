"""Portfolio Strategist — LLM agent with tool-calling trading autonomy.

Replaces the old per-ticker SignalRouter approach with a single LLM call
that sees ALL dossiers, the full portfolio state, and decides:
  • Which stocks to buy/sell
  • How many shares of each
  • What stop-loss / take-profit to set

Uses a structured JSON action loop:
  1. LLM receives portfolio + all dossiers + tool descriptions
  2. LLM outputs one action at a time as JSON
  3. We execute the action and feed the result back
  4. Repeat until LLM outputs {"action": "finish", ...}
"""

from __future__ import annotations

import json

from app.config import settings
from app.services.strategist_audit import StrategistAudit
from app.services.deep_analysis_service import DeepAnalysisService
from app.services.llm_service import LLMService
from app.services.paper_trader import PaperTrader
from app.services.peer_fetcher import PeerFetcher
from app.services.research_tools import (
    RESEARCH_TOOL_DESCRIPTIONS,
    RESEARCH_TOOL_NAMES,
    TOOL_REGISTRY as RESEARCH_TOOL_REGISTRY,
)
from app.services.yfinance_service import YFinanceCollector
from app.utils.logger import logger

_MAX_TURNS = 10  # Safety cap on LLM action loops
_AUTO_CLAMP_ENABLED = True  # Auto-reduce oversized orders instead of rejecting
_SYSTEM_PROMPT_TOKENS = 2500  # Approx tokens used by system prompt + tools
_TOKENS_PER_TURN = 1000  # Approx tokens per assistant+user pair (tool results)

# ── Tool descriptions (sent to LLM in system prompt) ──────────────
TOOL_DESCRIPTIONS = """\
You have these tools available. To use one, respond with a JSON object:
{"action": "<tool_name>", "params": {<tool_params>}}

### get_portfolio
Returns current cash, positions, total value, realized P&L.
Params: none
Example: {"action": "get_portfolio", "params": {}}

### get_market_overview
Returns a COMPACT list of all analyzed tickers with scores and metadata.
Use this FIRST to see what's available, then call get_dossier for tickers
you want to investigate further.
Params: none
Example: {"action": "get_market_overview", "params": {}}

### get_dossier
Returns the FULL analysis for ONE ticker: executive summary, bull/bear case,
key catalysts, and data quality notes. Call this only for tickers you want to
investigate for potential trades.
Params: ticker (str)
Example: {"action": "get_dossier", "params": {"ticker": "NVDA"}}

### get_sector_peers
MANDATORY before any buy. Returns 2-3 competitor stocks with their
fundamentals (P/E, revenue growth, margins, market cap) for comparative
analysis. Always call this to validate your pick is the best in its sector.
Params: ticker (str) — the ticker to find peers for
Example: {"action": "get_sector_peers", "params": {"ticker": "NVDA"}}

### place_buy
Buy shares of a stock.
Params: ticker (str), qty (int), reason (str)
Example: {"action": "place_buy", "params": {"ticker": "AAPL", "qty": 10, "reason": "Strong momentum + AI catalyst"}}

⚠️ BUDGET RULES (orders violating these are auto-clamped to safe qty):
- Max single order: 40% of portfolio value
- Max position per ticker: 25% of portfolio
- HOW TO CALCULATE QTY: qty = floor(cash_available * target_pct / price)
  Example: $100k cash, 15% position, $264/share → qty = floor(100000 * 0.15 / 264) = 56 shares
- Suggested allocation per position: 10-20% of portfolio (NOT 50-100%)

### place_sell
Sell shares of a stock you own.
Params: ticker (str), qty (int), reason (str)
Example: {"action": "place_sell", "params": {"ticker": "META", "qty": 5, "reason": "Thesis deteriorated, freeing capital"}}

### set_triggers
Set stop-loss and take-profit for a position.
Params: ticker (str), stop_loss_pct (float), take_profit_pct (float)
Example: {"action": "set_triggers", "params": {"ticker": "AAPL", "stop_loss_pct": 5.0, "take_profit_pct": 15.0}}

### get_market_status
Check if the market is open/closed and next open/close times.
Params: none
Example: {"action": "get_market_status", "params": {}}

### remove_from_watchlist
Remove a ticker from the watchlist permanently (pump-and-dump, penny stock, bad thesis).
Params: ticker (str), reason (str)
Example: {"action": "remove_from_watchlist", "params": {"ticker": "XYZZ", "reason": "Penny stock pump-and-dump, no real business"}}

### schedule_wakeup
Schedule the bot to wake up later and re-analyze a specific ticker.
Use when: earnings coming soon, pending FDA decision, awaiting data.
Params: ticker (str), delay_minutes (int), reason (str)
Example: {"action": "schedule_wakeup", "params": {"ticker": "NVDA", "delay_minutes": 120, "reason": "Earnings report in 2 hours"}}

### finish
Signal that you've made all your decisions for this cycle.
Params: summary (str) — brief reasoning for your decisions
Example: {"action": "finish", "params": {"summary": "Bought AAPL and NVDA, sold META..."}}

IMPORTANT RULES:
- Respond with ONLY ONE JSON action per message.
- You may wrap your response in ```json markdown fences if needed.
- After each action, you'll receive the result. Then decide your next action.
- You MUST call "finish" when done. Do NOT keep calling tools forever.
- Think about total portfolio allocation before placing trades.
- Do NOT call place_buy for tickers you already own — the system handles
  DCA automatically. Focus on NEW tickers not in your portfolio.
- Portfolio + market data is already provided — jump straight to analysis.
  Call get_dossier ONLY for tickers you're considering trading.
  Do NOT call get_dossier for every ticker — be selective.

"""
# Append research tools to tool descriptions
TOOL_DESCRIPTIONS += RESEARCH_TOOL_DESCRIPTIONS.replace(
    '{"tool":', '{"action":',
)

# JSON Schema for structured output — enforces the LLM can ONLY produce
# valid {"action": "...", "params": {...}} objects, not free-form text.
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "get_portfolio",
                "get_market_overview",
                "get_dossier",
                "get_sector_peers",
                "place_buy",
                "place_sell",
                "set_triggers",
                "get_market_status",
                "remove_from_watchlist",
                "schedule_wakeup",
                "finish",
                # Research tools
                *RESEARCH_TOOL_NAMES,
            ],
        },
        "params": {
            "type": "object",
        },
    },
    "required": ["action", "params"],
}


class PortfolioStrategist:
    """LLM-driven portfolio manager with tool-calling autonomy."""

    def __init__(
        self,
        paper_trader: PaperTrader,
        tickers: list[str],
        audit: StrategistAudit | None = None,
    ) -> None:
        self._trader = paper_trader
        self._tickers = tickers
        self._llm = LLMService()
        self._prompt_path = settings.PROMPTS_DIR / "portfolio_strategist.md"
        self._actions_log: list[dict] = []
        self._audit = audit or StrategistAudit()
        # Track tickers that have failed a buy — prevent retry loops
        self._failed_buy_tickers: set[str] = set()
        # Compact portfolio state injected each turn (bounded, not growing)
        self._portfolio_state: dict = {
            "trades_this_session": [],  # Capped at 10 most recent
            "positions_snapshot": {},   # ticker -> {qty, entry_price}
            "cash_after_trades": None,
        }

    async def run(self) -> dict:
        """Execute the full strategist loop — returns a summary dict."""
        logger.info(
            "[Strategist] Starting with %d candidate tickers", len(self._tickers),
        )

        # Build the system prompt
        system_prompt = self._build_system_prompt()

        # ── Force-inject portfolio + market data BEFORE the LLM loop ──
        # Instead of hoping the LLM calls these tools, we call them in code
        # and inject the results so the LLM starts with real data.
        portfolio_data = await self._tool_get_portfolio({})
        market_data = await self._tool_get_market_overview({})

        logger.info(
            "[Strategist] Auto-injected: portfolio=$%.0f cash, %d positions | "
            "market=%d candidates",
            portfolio_data.get("cash_balance", 0),
            portfolio_data.get("position_count", 0),
            market_data.get("total_new", market_data.get("total", 0)),
        )

        # ── Cash pre-check: skip LLM loop if cash can't buy anything ──
        cash = portfolio_data.get("cash_balance", 0)
        candidate_prices = [
            c["price"] for c in market_data.get("candidates", [])
            if c.get("price") and c["price"] > 0
        ]
        min_price = min(candidate_prices) if candidate_prices else float("inf")
        if cash < min_price and candidate_prices:
            logger.warning(
                "[Strategist] Insufficient cash ($%.2f) to buy cheapest "
                "candidate ($%.2f). Skipping LLM loop.",
                cash, min_price,
            )
            self._audit.log_finish(
                f"Skipped: insufficient cash (${cash:.2f} < ${min_price:.2f})",
                [],
            )
            audit_path = self._audit.generate_report()
            return {
                "orders_placed": 0,
                "orders": [],
                "triggers_set": 0,
                "triggers": [],
                "turns_used": 0,
                "summary": f"Insufficient cash (${cash:.2f}) to buy cheapest candidate (${min_price:.2f})",
                "actions_log": [],
                "audit_report": audit_path,
            }

        # Build held-tickers warning for prompt injection
        held_tickers = [
            p["ticker"] for p in portfolio_data.get("positions", [])
        ]
        held_section = ""
        if held_tickers:
            held_section = (
                "\n\n## ⛔ ALREADY OWNED — DO NOT BUY THESE\n"
                f"You already hold: {', '.join(held_tickers)}. "
                "Do NOT attempt to buy any of these tickers. "
                "The system auto-handles DCA. Focus on NEW tickers only."
            )

        # Pre-seed the conversation with the data the LLM needs
        conversation: list[dict] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "You are the Portfolio Strategist. Here is your current "
                    "portfolio and market data. Analyze it and make trading "
                    "decisions.\n\n"
                    "## Your Portfolio\n"
                    f"```json\n{json.dumps(portfolio_data, indent=2)}\n```\n\n"
                    "## Market Overview (all analyzed tickers)\n"
                    f"```json\n{json.dumps(market_data, indent=2)}\n```"
                    f"{held_section}\n\n"
                    "NOW: Review the candidates above. Call get_dossier on "
                    "your top 3-5 picks to see their full analysis, then "
                    "place_buy for stocks that meet your criteria. "
                    "You have cash to deploy — USE IT."
                ),
            },
        ]

        # Mark these tools as already used
        orders_placed: list[dict] = []
        triggers_set: list[dict] = []
        finish_summary = ""

        for turn in range(_MAX_TURNS):
            logger.info("[Strategist] Turn %d/%d", turn + 1, _MAX_TURNS)

            try:
                raw = await self._llm.chat(
                    messages=conversation,
                    response_format="json",
                    schema=ACTION_SCHEMA,
                    max_tokens=2000,
                )
            except Exception as exc:
                logger.error("[Strategist] LLM call failed: %s", exc)
                finish_summary = f"LLM call failed: {exc}"
                break

            # Parse the action — with rescue logic for multi-object responses
            action_data = None
            cleaned = LLMService.clean_json_response(raw)
            try:
                action_data = json.loads(cleaned)
            except (json.JSONDecodeError, ValueError):
                # Rescue attempt: the LLM may have wrapped JSON in prose.
                # clean_json_response now extracts the first complete {}.
                # If it still fails, try to salvage any valid JSON object.
                logger.warning(
                    "[Strategist] Bad JSON from LLM (turn %d): %s",
                    turn, raw[:300],
                )

            if action_data is None:
                self._audit.log_bad_json(turn + 1, raw)
                # Specific error message so the LLM doesn't repeat the mistake
                conversation.append({"role": "assistant", "content": raw})
                conversation.append({
                    "role": "user",
                    "content": (
                        "ERROR: Your response was not valid JSON. "
                        "You MUST send EXACTLY ONE action per message — "
                        "no extra text, no numbering, no multiple actions. "
                        "Respond with ONLY this format, nothing else:\n"
                        '{"action": "place_buy", "params": {"ticker": "NVDA", '
                        '"qty": 10, "reason": "momentum"}}'
                    ),
                })
                continue

            action_name = action_data.get("action", "")
            params = action_data.get("params", {})

            logger.info(
                "[Strategist] Action: %s | Params: %s",
                action_name, json.dumps(params)[:200],
            )

            # ── Handle finish action ──
            # Data is pre-injected (portfolio + market overview) so the LLM
            # already has everything it needs. No premature-finish guardrail.
            if action_name == "finish":
                finish_summary = params.get("summary", "No summary provided")
                self._actions_log.append({
                    "action": "finish",
                    "summary": finish_summary,
                })
                self._audit.log_turn(
                    turn + 1, raw, "finish", params, {"summary": finish_summary},
                )
                break



            result = await self._execute_action(action_name, params)

            # Track orders and triggers
            if action_name == "place_buy" and result.get("status") == "filled":
                orders_placed.append(result)
            elif action_name == "place_sell" and result.get("status") == "filled":
                orders_placed.append(result)
            elif action_name == "set_triggers" and result.get("status") == "ok":
                triggers_set.append(result)

            self._actions_log.append({
                "action": action_name,
                "params": params,
                "result": result,
            })

            self._audit.log_turn(turn + 1, raw, action_name, params, result)

            # ── Track action in compact portfolio state ──
            if result.get("status") == "filled":
                trade_entry = {
                    "side": result.get("side", "?").upper(),
                    "ticker": result.get("ticker", "?"),
                    "qty": result.get("qty", 0),
                    "price": result.get("price", 0),
                }
                trades = self._portfolio_state["trades_this_session"]
                trades.append(trade_entry)
                # Keep only last 10 trades to bound memory size
                if len(trades) > 10:
                    self._portfolio_state["trades_this_session"] = trades[-10:]

            # Update positions snapshot from paper trader
            try:
                positions = self._trader.get_positions()
                self._portfolio_state["positions_snapshot"] = {
                    p["ticker"]: {
                        "qty": p["qty"],
                        "entry": round(p["avg_entry_price"], 2),
                    }
                    for p in positions
                }
                self._portfolio_state["cash_after_trades"] = round(
                    self._trader.get_cash_balance(), 2,
                )
            except Exception:
                pass  # Non-critical — portfolio tool gives this too

            # Feed the result back to the LLM
            conversation.append({"role": "assistant", "content": raw})
            # If a buy/sell failed, give the LLM a stronger nudge
            result_text = json.dumps(result, indent=2)
            if result.get("error") and action_name in ("place_buy", "place_sell"):
                failed_ticker = params.get("ticker", "")
                result_text += (
                    f"\n\nIMPORTANT: {failed_ticker} buy/sell FAILED. "
                    f"Do NOT retry {failed_ticker}. "
                    f"Move on to a DIFFERENT ticker immediately."
                )
            conversation.append({
                "role": "user",
                "content": f"Tool result:\n{result_text}",
            })

            # ── Sliding window: keep conversation bounded ──
            # Dynamic based on user's context_size setting so larger
            # contexts retain more history and smaller ones stay safe.
            ctx = settings.LLM_CONTEXT_SIZE
            usable_tokens = max(ctx - _SYSTEM_PROMPT_TOKENS, 2000)
            # Each turn pair is ~_TOKENS_PER_TURN tokens; keep 70% budget
            # for conversation (30% reserved for next LLM response)
            max_msgs = max(4, int(usable_tokens * 0.7 / _TOKENS_PER_TURN) * 2)
            # conversation[0] = initial user prompt (always keep).
            overflow = len(conversation) - 1
            if overflow > max_msgs:
                trim_count = overflow - max_msgs
                conversation = [conversation[0]] + conversation[1 + trim_count:]

            # ── Portfolio state: inject compact JSON so LLM stays oriented ──
            if self._portfolio_state["trades_this_session"]:
                state_json = json.dumps(self._portfolio_state, indent=None)
                memory_text = (
                    "PORTFOLIO STATE (your trades this session + current "
                    "positions — do NOT repeat completed trades):\n"
                    f"{state_json}"
                )
                # Replace or append state in first user message
                base_prompt = conversation[0]["content"]
                # Strip any previous state block
                if "PORTFOLIO STATE" in base_prompt:
                    base_prompt = base_prompt[:base_prompt.index("PORTFOLIO STATE")].rstrip()
                conversation[0] = {
                    "role": "user",
                    "content": f"{base_prompt}\n\n{memory_text}",
                }
        else:
            finish_summary = "Max turns reached — auto-finishing"
            logger.warning("[Strategist] Hit max turns (%d)", _MAX_TURNS)

        # Finalize audit
        self._audit.log_finish(finish_summary, orders_placed)
        audit_path = self._audit.generate_report()

        summary = {
            "orders_placed": len(orders_placed),
            "orders": orders_placed,
            "triggers_set": len(triggers_set),
            "triggers": triggers_set,
            "turns_used": min(turn + 1, _MAX_TURNS) if 'turn' in dir() else 0,
            "summary": finish_summary,
            "actions_log": self._actions_log,
            "audit_report": audit_path,
        }

        logger.info(
            "[Strategist] Done — %d orders, %d triggers. Summary: %s",
            len(orders_placed), len(triggers_set), finish_summary[:200],
        )
        logger.info("[Strategist] Audit report: %s", audit_path)
        return summary

    # ── Tool executors ─────────────────────────────────────────────

    async def _execute_action(
        self, action_name: str, params: dict,
    ) -> dict:
        """Route an action to the appropriate executor."""
        executors = {
            "get_portfolio": self._tool_get_portfolio,
            "get_market_overview": self._tool_get_market_overview,
            "get_dossier": self._tool_get_dossier,
            # Legacy alias for backwards compatibility with older prompts
            "get_all_candidates": self._tool_get_market_overview,
            "get_sector_peers": self._tool_get_sector_peers,
            "place_buy": self._tool_place_buy,
            "place_sell": self._tool_place_sell,
            "set_triggers": self._tool_set_triggers,
            "get_market_status": self._tool_get_market_status,
            "remove_from_watchlist": self._tool_remove_from_watchlist,
            "schedule_wakeup": self._tool_schedule_wakeup,
            # Research tools (from research_tools.py)
            **RESEARCH_TOOL_REGISTRY,
        }

        executor = executors.get(action_name)
        if not executor:
            return {"error": f"Unknown tool: {action_name}"}

        try:
            return await executor(params)
        except Exception as exc:
            logger.error("[Strategist] Tool %s failed: %s", action_name, exc)
            return {"error": str(exc)}

    async def _tool_get_portfolio(self, _params: dict) -> dict:
        """Return current portfolio state."""
        portfolio = self._trader.get_portfolio()
        orders_today = self._trader.get_orders_today_count()
        daily_pnl = self._trader.get_daily_pnl_pct()
        return {
            "cash_balance": portfolio["cash_balance"],
            "total_portfolio_value": portfolio["total_portfolio_value"],
            "positions": portfolio.get("positions", []),
            "position_count": len(portfolio.get("positions", [])),
            "realized_pnl": portfolio.get("realized_pnl", 0),
            "orders_today": orders_today,
            "daily_pnl_pct": round(daily_pnl, 2),
        }

    async def _tool_get_market_overview(self, _params: dict) -> dict:
        """Return compact metadata for all analyzed tickers (no prose)."""
        candidates = []
        positions = self._trader.get_positions()
        held_map = {p["ticker"]: p["qty"] for p in positions}

        for ticker in self._tickers:
            dossier = DeepAnalysisService.get_latest_dossier(ticker)
            if not dossier:
                continue

            # Get current price
            try:
                from app.main import _fetch_one_quote
                quote = _fetch_one_quote(ticker)
                price = quote.get("price") if quote else None
            except Exception:
                price = None

            scorecard = dossier.get("scorecard", {})

            # Compact entry — metadata only, no prose
            candidates.append({
                "ticker": ticker,
                "sector": dossier.get("sector", "Unknown"),
                "conviction": dossier.get("conviction_score", 0.5),
                "trend_score": scorecard.get("trend_template_score", 0),
                "vcp_score": scorecard.get("vcp_setup_score", 0),
                "rs_rating": scorecard.get("relative_strength_rating", 0),
                "signal": scorecard.get("signal_summary", "")[:80],
                "price": price,
                "held_qty": held_map.get(ticker, 0),
            })

        # Sort by conviction (highest first)
        candidates.sort(key=lambda c: c["conviction"], reverse=True)

        # Log candidates to audit
        self._audit.log_candidates(candidates)

        # Split into new candidates vs already-held positions
        new_candidates = [c for c in candidates if c.get("held_qty", 0) == 0]
        held_positions = [c for c in candidates if c.get("held_qty", 0) > 0]
        held_tickers = [c["ticker"] for c in held_positions]

        # Build sector summary
        sector_counts: dict[str, int] = {}
        for c in candidates:
            s = c.get("sector", "Unknown")
            sector_counts[s] = sector_counts.get(s, 0) + 1

        result: dict = {
            "candidates": new_candidates,
            "total_new": len(new_candidates),
            "sector_breakdown": sector_counts,
            "note": (
                "These are NEW candidates you can BUY. "
                "Call get_dossier(ticker) for full analysis."
            ),
        }
        if held_tickers:
            result["already_held"] = held_tickers
            result["held_warning"] = (
                f"⛔ DO NOT BUY: {', '.join(held_tickers)} — "
                f"you already own these. Focus only on candidates above."
            )
        return result

    async def _tool_get_dossier(self, params: dict) -> dict:
        """Return the full analysis prose for ONE ticker."""
        ticker = str(params.get("ticker", "")).upper().strip()
        if not ticker:
            return {"error": "Missing 'ticker' parameter"}

        dossier = DeepAnalysisService.get_latest_dossier(ticker)
        if not dossier:
            return {"error": f"No dossier found for {ticker}"}

        scorecard = dossier.get("scorecard", {})

        # Flag data gaps for the LLM
        data_gaps: list[str] = []
        if not dossier.get("executive_summary"):
            data_gaps.append("no executive summary")
        if not dossier.get("bull_case"):
            data_gaps.append("no bull case")
        if not dossier.get("bear_case"):
            data_gaps.append("no bear case")
        if not dossier.get("key_catalysts"):
            data_gaps.append("no catalysts identified")
        if not scorecard.get("trend_template_score"):
            data_gaps.append("missing trend score")
        if not scorecard.get("vcp_setup_score"):
            data_gaps.append("missing VCP score")

        # Get current price
        try:
            from app.main import _fetch_one_quote
            quote = _fetch_one_quote(ticker)
            price = quote.get("price") if quote else None
        except Exception:
            price = None

        return {
            "ticker": ticker,
            "sector": dossier.get("sector", "Unknown"),
            "industry": dossier.get("industry", "Unknown"),
            "market_cap_tier": dossier.get("market_cap_tier", "unknown"),
            "conviction_score": dossier.get("conviction_score", 0.5),
            "signal_summary": scorecard.get("signal_summary", ""),
            # Full analysis prose (the deep dive)
            "executive_summary": dossier.get("executive_summary", ""),
            "bull_case": dossier.get("bull_case", ""),
            "bear_case": dossier.get("bear_case", ""),
            "key_catalysts": dossier.get("key_catalysts", []),
            # Market data
            "current_price": price,
            # Data quality
            "data_gaps": data_gaps if data_gaps else None,
        }

    async def _tool_get_sector_peers(self, params: dict) -> dict:
        """Return 2-3 peer stocks with fundamentals for comparison.

        Strategy:
        1. Check watchlist for same-sector tickers with dossiers
        2. If < 2 watchlist peers, use PeerFetcher to discover competitors
           and fetch their fundamentals from yfinance
        """
        ticker = str(params.get("ticker", "")).upper().strip()
        if not ticker:
            return {"error": "Missing 'ticker' parameter"}

        # Get the target ticker's dossier for sector info
        target_dossier = DeepAnalysisService.get_latest_dossier(ticker)
        if not target_dossier:
            return {"error": f"No dossier found for {ticker}"}

        target_sector = target_dossier.get("sector", "Unknown")
        target_industry = target_dossier.get(
            "scorecard", {},
        ).get("industry", "Unknown")

        # ── Step 1: Check watchlist for same-sector peers ──
        peers = []
        for t in self._tickers:
            if t == ticker:
                continue
            dossier = DeepAnalysisService.get_latest_dossier(t)
            if not dossier:
                continue
            peer_sector = dossier.get("sector", "Unknown")
            if peer_sector == target_sector and peer_sector != "Unknown":
                scorecard = dossier.get("scorecard", {})
                peers.append({
                    "ticker": t,
                    "source": "watchlist",
                    "sector": peer_sector,
                    "industry": dossier.get("industry", "Unknown"),
                    "conviction_score": dossier.get("conviction_score", 0.5),
                    "trend_score": scorecard.get("trend_template_score", 0),
                    "vcp_score": scorecard.get("vcp_setup_score", 0),
                    "rs_rating": scorecard.get(
                        "relative_strength_rating", 0,
                    ),
                    "executive_summary": dossier.get(
                        "executive_summary", "",
                    )[:300],
                    "bull_case": dossier.get("bull_case", "")[:200],
                })

        # ── Step 2: If < 2 watchlist peers, discover competitors ──
        if len(peers) < 2:
            logger.info(
                "[Strategist] Only %d watchlist peers for %s — "
                "discovering competitors via PeerFetcher",
                len(peers), ticker,
            )
            try:
                llm = LLMService()
                fetcher = PeerFetcher(llm)
                yf = YFinanceCollector()

                # Get fundamentals for sector/industry context
                target_fundamentals = await yf.collect_fundamentals(ticker)
                discovered = await fetcher.get_industry_peers(
                    ticker, target_fundamentals,
                )

                # Fetch fundamentals for each discovered peer
                existing_tickers = {p["ticker"] for p in peers}
                for peer_ticker in discovered:
                    if peer_ticker in existing_tickers or peer_ticker == ticker:
                        continue
                    try:
                        fund = await yf.collect_fundamentals(peer_ticker)
                        if fund:
                            peers.append({
                                "ticker": peer_ticker,
                                "source": "discovered",
                                "sector": fund.sector or target_sector,
                                "industry": fund.industry or "Unknown",
                                "market_cap": getattr(fund, "market_cap", 0),
                                "pe_ratio": getattr(fund, "trailing_pe", None),
                                "forward_pe": getattr(fund, "forward_pe", None),
                                "revenue_growth": getattr(
                                    fund, "revenue_growth", None,
                                ),
                                "profit_margin": getattr(
                                    fund, "profit_margin", None,
                                ),
                                "current_price": getattr(
                                    fund, "current_price", None,
                                ),
                            })
                    except Exception as exc:
                        logger.warning(
                            "[Strategist] Failed to fetch peer %s: %s",
                            peer_ticker, exc,
                        )
            except Exception as exc:
                logger.warning(
                    "[Strategist] PeerFetcher failed for %s: %s",
                    ticker, exc,
                )

        # Sort by conviction (watchlist) or market_cap (discovered)
        peers.sort(
            key=lambda p: p.get("conviction_score", 0)
            or p.get("market_cap", 0),
            reverse=True,
        )
        peers = peers[:3]

        return {
            "target_ticker": ticker,
            "target_sector": target_sector,
            "target_industry": target_industry,
            "peers_found": len(peers),
            "peers": peers,
            "note": (
                f"Compare {ticker} against these sector peers. "
                f"Is {ticker} the best opportunity in {target_sector}? "
                f"Consider relative valuation, growth, and momentum."
            ),
        }

    async def _tool_place_buy(self, params: dict) -> dict:
        """Execute a buy order via PaperTrader."""
        ticker = params.get("ticker", "")
        qty = int(params.get("qty", 0))
        reason = params.get("reason", "")

        if not ticker or qty <= 0:
            return {"error": "ticker and qty (>0) are required"}

        # Guard: prevent retry loops on previously failed tickers
        if ticker in self._failed_buy_tickers:
            return {
                "error": (
                    f"ALREADY FAILED: {ticker} buy was already attempted and "
                    f"failed this session. Do NOT retry — pick a DIFFERENT "
                    f"ticker from your candidates list."
                ),
            }

        # Guard: check if we already hold this ticker (before any work)
        positions = self._trader.get_positions()
        held_tickers = {p["ticker"] for p in positions}
        if ticker in held_tickers:
            self._failed_buy_tickers.add(ticker)
            return {
                "error": (
                    f"POSITION EXISTS: You already own {ticker}. "
                    f"The system auto-handles DCA (dollar-cost averaging). "
                    f"Choose a DIFFERENT ticker to buy instead."
                ),
            }

        # Get current price
        try:
            from app.main import _fetch_one_quote
            quote = _fetch_one_quote(ticker)
            price = quote.get("price") if quote else None
        except Exception:
            price = None

        if not price:
            self._failed_buy_tickers.add(ticker)
            return {"error": f"Could not fetch price for {ticker}"}

        # Safety check: don't let LLM blow the entire account
        portfolio = self._trader.get_portfolio()
        order_cost = price * qty
        cash = portfolio["cash_balance"]
        total_value = portfolio["total_portfolio_value"]

        # ── Auto-clamp: calculate the maximum safe qty ──
        max_by_cash = int(cash / price) if price > 0 else 0
        max_by_pct = int(total_value * 0.40 / price) if price > 0 else 0
        max_safe_qty = min(max_by_cash, max_by_pct)
        # Also respect the 25% concentration limit
        positions = self._trader.get_positions()
        existing_value = 0.0
        for p in positions:
            if p["ticker"] == ticker:
                existing_value = p["qty"] * price
                break
        max_by_conc = int((total_value * 0.25 - existing_value) / price) if price > 0 else 0
        max_safe_qty = min(max_safe_qty, max(max_by_conc, 0))

        clamped = False
        if qty > max_safe_qty and _AUTO_CLAMP_ENABLED and max_safe_qty > 0:
            logger.warning(
                "[Strategist] Auto-clamping %s buy: %d -> %d shares "
                "(cash=$%.0f, portfolio=$%.0f, price=$%.2f)",
                ticker, qty, max_safe_qty, cash, total_value, price,
            )
            qty = max_safe_qty
            order_cost = price * qty
            clamped = True
        elif max_safe_qty <= 0:
            self._failed_buy_tickers.add(ticker)
            return {
                "error": (
                    f"Cannot buy {ticker}: max safe qty is 0. "
                    f"Cash=${cash:.2f}, portfolio=${total_value:.2f}, "
                    f"price=${price:.2f}, existing=${existing_value:.0f}. "
                    f"Move on to a different ticker."
                ),
            }
        elif order_cost > cash:
            self._failed_buy_tickers.add(ticker)
            return {
                "error": (
                    f"Insufficient cash. "
                    f"Order=${order_cost:.2f}, Cash=${cash:.2f}. "
                    f"Max affordable: {max_by_cash} shares"
                ),
            }

        # The 25% concentration limit is already handled by auto-clamp above

        try:
            order = self._trader.buy(
                ticker=ticker,
                qty=qty,
                price=price,
                conviction_score=0.0,
                signal=f"STRATEGIST_BUY: {reason[:100]}",
            )
        except Exception as exc:
            # Catch DuckDB constraint errors and other DB issues
            self._failed_buy_tickers.add(ticker)
            error_msg = str(exc)
            if "Duplicate key" in error_msg or "primary key" in error_msg.lower():
                return {
                    "error": (
                        f"POSITION EXISTS: You already own {ticker}. "
                        f"The system auto-handles DCA (dollar-cost averaging). "
                        f"This means {ticker} is already in your portfolio. "
                        f"Choose a DIFFERENT ticker to buy instead."
                    ),
                }
            return {"error": f"Buy failed for {ticker}: {error_msg}"}

        if order:
            from app.services.event_logger import log_event
            log_event(
                "trading",
                "strategist_buy",
                f"${ticker}: BUY {order.qty} shares @ ${order.price:.2f} — {reason}",
                ticker=ticker,
                metadata={
                    "qty": order.qty,
                    "price": order.price,
                    "reason": reason,
                    "source": "portfolio_strategist",
                },
            )
            result = {
                "status": "filled",
                "ticker": ticker,
                "side": "buy",
                "qty": order.qty,
                "price": order.price,
                "total_cost": order.qty * order.price,
                "reason": reason,
            }
            if clamped:
                result["clamped"] = True
                result["original_qty"] = params.get("qty", 0)
                result["note"] = (
                    f"Order was auto-clamped from {params.get('qty', 0)} to "
                    f"{order.qty} shares to stay within risk limits."
                )
            return result
        self._failed_buy_tickers.add(ticker)
        return {"error": f"Order rejected by paper trader for {ticker}"}

    async def _tool_place_sell(self, params: dict) -> dict:
        """Execute a sell order via PaperTrader."""
        ticker = params.get("ticker", "")
        qty = int(params.get("qty", 0))
        reason = params.get("reason", "")

        if not ticker or qty <= 0:
            return {"error": "ticker and qty (>0) are required"}

        # Get current price
        try:
            from app.main import _fetch_one_quote
            quote = _fetch_one_quote(ticker)
            price = quote.get("price") if quote else None
        except Exception:
            price = None

        if not price:
            return {"error": f"Could not fetch price for {ticker}"}

        order = self._trader.sell(
            ticker=ticker,
            qty=qty,
            price=price,
            conviction_score=0.0,
            signal=f"STRATEGIST_SELL: {reason[:100]}",
        )

        if order:
            from app.services.event_logger import log_event
            log_event(
                "trading",
                "strategist_sell",
                f"${ticker}: SELL {order.qty} shares @ ${order.price:.2f} — {reason}",
                ticker=ticker,
                metadata={
                    "qty": order.qty,
                    "price": order.price,
                    "reason": reason,
                    "source": "portfolio_strategist",
                },
            )
            return {
                "status": "filled",
                "ticker": ticker,
                "side": "sell",
                "qty": order.qty,
                "price": order.price,
                "total_proceeds": order.qty * order.price,
                "reason": reason,
            }
        return {"error": f"Sell rejected for {ticker} (do you hold {qty} shares?)"}

    async def _tool_set_triggers(self, params: dict) -> dict:
        """Set stop-loss and take-profit for a position."""
        ticker = params.get("ticker", "")
        stop_loss_pct = float(params.get("stop_loss_pct", 5.0))
        take_profit_pct = float(params.get("take_profit_pct", 15.0))

        if not ticker:
            return {"error": "ticker is required"}

        # Find the position
        positions = self._trader.get_positions()
        pos = None
        for p in positions:
            if p["ticker"] == ticker:
                pos = p
                break

        if not pos:
            return {"error": f"No open position for {ticker}"}

        self._trader.set_triggers_for_position(
            ticker=ticker,
            entry_price=pos["avg_entry_price"],
            qty=pos["qty"],
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )

        return {
            "status": "ok",
            "ticker": ticker,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "stop_price": round(pos["avg_entry_price"] * (1 - stop_loss_pct / 100), 2),
            "target_price": round(pos["avg_entry_price"] * (1 + take_profit_pct / 100), 2),
        }

    async def _tool_get_market_status(self, _params: dict) -> dict:
        """Return current market status."""
        from app.utils.market_hours import market_status
        return market_status()

    async def _tool_remove_from_watchlist(self, params: dict) -> dict:
        """Remove a junk ticker from the watchlist."""
        ticker = str(params.get("ticker", "")).upper().strip()
        reason = str(params.get("reason", "No reason given"))
        if not ticker:
            return {"error": "Missing 'ticker' parameter"}

        from app.services.watchlist_manager import WatchlistManager
        result = WatchlistManager().remove_ticker(ticker)
        logger.info(
            "[Strategist] Removed %s from watchlist: %s", ticker, reason,
        )
        return {**result, "reason": reason}

    async def _tool_schedule_wakeup(self, params: dict) -> dict:
        """Schedule a future re-analysis for a specific ticker."""
        ticker = str(params.get("ticker", "")).upper().strip()
        delay = int(params.get("delay_minutes", 60))
        reason = str(params.get("reason", "Scheduled re-check"))
        if not ticker:
            return {"error": "Missing 'ticker' parameter"}

        # Access the global scheduler instance
        try:
            from app.main import _scheduler
            result = _scheduler.add_one_shot_job(ticker, delay, reason)
            return result
        except Exception as exc:
            logger.error("[Strategist] Failed to schedule wakeup: %s", exc)
            return {"error": str(exc)}

    # ── Helpers ─────────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """Construct the full system prompt with tool descriptions."""
        # Load the base strategy prompt
        if self._prompt_path.exists():
            strategy = self._prompt_path.read_text(encoding="utf-8")
        else:
            strategy = "You are an autonomous trading strategist."

        # Load user's custom strategy (editable via UI)
        user_strategy_path = settings.USER_CONFIG_DIR / "strategy.md"
        user_strategy = ""
        if user_strategy_path.exists():
            raw = user_strategy_path.read_text(encoding="utf-8").strip()
            if raw:
                user_strategy = f"\n\n## User Custom Strategy\n\n{raw}"

        # ── Cash awareness header ──────────────────────────────
        # Inject current cash balance so the LLM knows upfront
        # whether BUY actions are feasible (prevents wasted turns)
        cash = self._trader.get_cash_balance()
        cash_header = (
            f"⚠️ CURRENT CASH AVAILABLE: ${cash:,.2f}\n"
            f"{'DO NOT attempt BUY orders — insufficient funds.' if cash < 50 else ''}\n\n"
        )

        return f"{cash_header}{strategy}{user_strategy}\n\n{TOOL_DESCRIPTIONS}"

    # NOTE: _format_conversation was removed. LLMService.chat() now accepts
    # a native messages array, so multi-turn conversations are passed
    # directly to Ollama without flattening.
