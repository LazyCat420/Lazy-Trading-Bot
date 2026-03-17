"""Trading Pipeline Service — single orchestrator from symbols → decisions → execution.

Replaces the multi-step PortfolioStrategist loop with:
  1. Load symbols from watchlist
  2. Filter through FilterPipeline
  3. Build context per ticker (prices, technicals, dossier)
  4. Ask TradingAgent for a decision (one LLM call)
  5. Execute through ExecutionService (with safety gates)
  6. Log everything to trade_decisions + trade_executions
"""

from __future__ import annotations

import contextlib
import time
import uuid

import yfinance as yf

from app.services.artifact_logger import ArtifactLogger
from app.services.decision_logger import DecisionLogger
from app.services.deep_analysis_service import DeepAnalysisService
from app.services.event_logger import log_event
from app.services.execution_service import ExecutionService
from app.services.paper_trader import PaperTrader
from app.services.symbol_filter import get_filter_pipeline
from app.services.trading_agent import TradingAgent
from app.services.WorkflowService import WorkflowTracker
from app.utils.logger import logger


class TradingPipelineService:
    """One-shot trading pipeline: symbols → decisions → execution."""

    def __init__(
        self,
        paper_trader: PaperTrader,
        *,
        dry_run: bool = True,
        bot_id: str = "default",
        query_vector_cache: dict[str, list[float]] | None = None,
    ) -> None:
        self._trader = paper_trader
        self._agent = TradingAgent()
        self._executor = ExecutionService(paper_trader)
        self._deep = DeepAnalysisService()
        self._artifacts = ArtifactLogger()
        self._dry_run = dry_run
        self._bot_id = bot_id
        self._query_vector_cache = query_vector_cache or {}

    async def run_once(
        self,
        tickers: list[str] | None = None,
    ) -> dict:
        """Run the full trading pipeline once.

        Args:
            tickers: Optional list of tickers. If None, uses active watchlist.

        Returns:
            Summary dict with per-ticker results.
        """
        t0 = time.time()
        cycle_id = str(uuid.uuid4())[:8]
        cycle_dir = self._artifacts.start_cycle(cycle_id)

        # ── Step 1: Get tickers ────────────────────────────────
        if not tickers:
            from app.services.watchlist_manager import WatchlistManager

            wm = WatchlistManager(bot_id=self._bot_id)
            tickers = wm.get_active_tickers()

        if not tickers:
            logger.info("[TradingPipeline] No active tickers — nothing to do")
            return {"decisions": 0, "executions": 0, "tickers": []}

        logger.info(
            "[TradingPipeline] Processing %d tickers: %s",
            len(tickers),
            ", ".join(tickers),
        )

        # ── Step 2: Filter symbols ─────────────────────────────
        pipeline = get_filter_pipeline()
        valid_tickers = []
        for t in tickers:
            result = pipeline.run(t, {"source": "trading_pipeline", "bot_id": self._bot_id})
            if result.passed:
                valid_tickers.append(t)
            else:
                logger.info("[TradingPipeline] Filtered out %s: %s", t, result.reason)

        if not valid_tickers:
            logger.info("[TradingPipeline] All tickers filtered out")
            return {"decisions": 0, "executions": 0, "tickers": [], "filtered": len(tickers)}

        # ── Step 3: Build context + decide + execute ───────────
        results = []

        # Priority sort: BUY-signal tickers first (most actionable),
        # then HOLD, then SELL — so we process the best opportunities
        # before burning LLM time on low-priority tickers.
        _SIGNAL_PRIORITY = {"BUY": 0, "PENDING": 1, "HOLD": 2, "SELL": 3}
        try:
            from app.services.watchlist_manager import WatchlistManager

            wm = WatchlistManager(bot_id=self._bot_id)
            signals = wm.get_ticker_signals()  # {ticker: signal}
            valid_tickers.sort(
                key=lambda t: _SIGNAL_PRIORITY.get(signals.get(t, "PENDING"), 1),
            )
            logger.info(
                "[TradingPipeline] Sorted %d tickers by signal priority",
                len(valid_tickers),
            )
        except Exception:
            pass  # Sort is best-effort

        orders_this_cycle = 0
        total_valid = len(valid_tickers)

        for tick_idx, ticker in enumerate(valid_tickers):
            logger.info(
                "[TradingPipeline] ➤ Ticker %d/%d: $%s — processing…",
                tick_idx + 1, total_valid, ticker,
            )
            # Refresh portfolio state for EVERY ticker so the LLM accurately 
            # sees its cash balance drain if it executes trades during this cycle.
            portfolio = self._trader.get_portfolio()

            try:
                result = await self._process_ticker(ticker, portfolio, cycle_dir, cycle_id)
                results.append(result)
                is_order = result.get("exec_status") in ("executed", "dry_run") and result.get(
                    "action"
                ) in ("BUY", "SELL")
                if is_order:
                    orders_this_cycle += 1
                logger.info(
                    "[TradingPipeline] ✅ Ticker %d/%d: $%s — %s (%s)",
                    tick_idx + 1, total_valid, ticker,
                    result.get("action", "?"),
                    result.get("exec_status", "?"),
                )
            except Exception as exc:
                logger.error("[TradingPipeline] Failed for %s: %s", ticker, exc)
                results.append(
                    {
                        "ticker": ticker,
                        "status": "error",
                        "error": str(exc),
                    }
                )

        # ── Summary ────────────────────────────────────────────
        elapsed = round(time.time() - t0, 1)
        decisions = [r for r in results if r.get("action")]
        executions = [r for r in results if r.get("exec_status") in ("executed", "dry_run")]

        summary = {
            "decisions": len(decisions),
            "executions": len(executions),
            "duration_seconds": elapsed,
            "tickers": results,
            "filtered": len(tickers) - len(valid_tickers),
        }

        # Count orders for compatibility with autonomous_loop health checks
        summary["orders"] = len(
            [
                r
                for r in results
                if r.get("exec_status") in ("executed", "dry_run")
                and r.get("action") in ("BUY", "SELL")
            ]
        )

        logger.info(
            "[TradingPipeline] Done: %d decisions, %d executions in %.1fs",
            len(decisions),
            len(executions),
            elapsed,
        )

        # Save cycle summary artifact
        with contextlib.suppress(Exception):
            self._artifacts.save_summary(cycle_dir, summary)

        # ── Auto-save workflow (Prism-style node graph) ─────────
        with contextlib.suppress(Exception):
            from app.services.llm_audit_logger import LLMAuditLogger
            from app.services.workflow_assembler import save_workflow
            audit_logs = LLMAuditLogger.get_logs_for_cycle(cycle_id)
            if audit_logs:
                wf_id = save_workflow(cycle_id, audit_logs)
                if wf_id:
                    summary["workflow_id"] = wf_id
                    logger.info(
                        "[TradingPipeline] Workflow saved: %s (%d steps)",
                        wf_id, len(audit_logs),
                    )

        return summary

    async def _process_ticker(
        self,
        ticker: str,
        portfolio: dict,
        cycle_dir=None,
        cycle_id: str = "",
    ) -> dict:
        """Process a single ticker: context → decide → execute."""
        # ── Build context ──────────────────────────────────────
        logger.info("[TradingPipeline] $%s — building context…", ticker)
        context = await self._build_context(ticker, portfolio)

        # Save context artifact
        if cycle_dir:
            with contextlib.suppress(Exception):
                self._artifacts.save_context(cycle_dir, ticker, context)

        # ── Get LLM decision ──────────────────────────────────
        logger.info("[TradingPipeline] $%s — requesting LLM decision…", ticker)
        action, raw_llm, llm_meta = await self._agent.decide(context, self._bot_id)

        # ── Post-LLM sanity check (BUY bias guardrail) ────────
        flags = set(context.get("quant_flags", []))
        conviction = context.get("dossier_conviction", 0.5)
        dossier_sig = context.get("dossier_signal", "UNKNOWN")

        if action.action == "BUY":
            override_reason = ""
            # Rule: Can't BUY against a SELL verdict
            if dossier_sig == "SELL":
                override_reason = f"quant verdict is SELL (conviction={conviction:.0%})"
            # Rule: Bankruptcy risk = forced HOLD
            elif "bankruptcy_risk_high" in flags:
                override_reason = "bankruptcy_risk_high flag present"
            # Rule: Drawdown + negative Sortino = forced SELL
            elif "drawdown_exceeds_20pct" in flags and "negative_sortino" in flags:
                override_reason = "drawdown_exceeds_20pct + negative_sortino"
                action.action = "SELL"
                action.confidence = min(action.confidence, 0.40)
            # Rule: Low conviction = forced HOLD
            elif conviction < 0.25 and action.confidence > 0.60:
                override_reason = (
                    f"conviction={conviction:.0%} too low for "
                    f"BUY confidence={action.confidence:.0%}"
                )

            if override_reason and action.action == "BUY":
                logger.warning(
                    "[TradingPipeline] BUY override for %s → HOLD: %s",
                    ticker,
                    override_reason,
                )
                action.action = "HOLD"
                action.confidence = min(action.confidence, 0.40)
                action.risk_notes = (
                    f"[GUARDRAIL] {override_reason}. Original: BUY. {action.risk_notes or ''}"
                )
            elif override_reason:
                # Action was changed to SELL by a specific rule above
                logger.warning(
                    "[TradingPipeline] BUY override for %s → %s: %s",
                    ticker,
                    action.action,
                    override_reason,
                )

        # ── Post-LLM sanity check: SELL without position ──────
        if action.action == "SELL":
            positions = portfolio.get("positions", [])
            held_tickers = {p["ticker"] for p in positions}
            if ticker not in held_tickers:
                logger.warning(
                    "[TradingPipeline] SELL override for %s → HOLD: no position held",
                    ticker,
                )
                action.action = "HOLD"
                action.confidence = 0.30
                action.risk_notes = (
                    f"[GUARDRAIL] Cannot SELL {ticker} — no position. "
                    f"Original: SELL. {action.risk_notes or ''}"
                )

        # ── Log decision ──────────────────────────────────────
        decision_id = DecisionLogger.log_decision(action, raw_llm)

        log_event(
            "trading",
            f"decision_{action.action.lower()}",
            f"${ticker}: {action.action} (confidence={action.confidence:.0%}) — {action.rationale[:100]}",
            ticker=ticker,
            metadata={
                "action": action.action,
                "confidence": action.confidence,
                "risk_level": action.risk_level,
            },
        )

        result = {
            "ticker": ticker,
            "action": action.action,
            "confidence": action.confidence,
            "rationale": action.rationale,
            "risk_level": action.risk_level,
            "decision_id": decision_id,
        }

        # (Workflow is assembled locally at end of cycle from audit logs)

        # Save decision artifact
        if cycle_dir:
            try:
                self._artifacts.save_decision(cycle_dir, ticker, action.model_dump())
                self._artifacts.save_response(cycle_dir, ticker, raw_llm)
            except Exception:
                pass

        # ── Execute ───────────────────────────────────────────
        if action.action in ("BUY", "SELL"):
            try:
                exec_result = await self._executor.execute(
                    action=action,
                    decision_id=decision_id,
                    dry_run=self._dry_run,
                    atr=context.get("atr", 0.0),
                    current_price=context.get("last_price", 0.0),
                )
            except Exception as exc:
                logger.error(
                    "[TradingPipeline] Execution failed for %s: %s",
                    ticker,
                    exc,
                )
                exec_result = {"status": "error", "reason": str(exc)}
            result["exec_status"] = exec_result.get("status", "unknown")
            result["exec_detail"] = exec_result

            # Save execution artifact
            if cycle_dir:
                with contextlib.suppress(Exception):
                    self._artifacts.save_execution(cycle_dir, ticker, exec_result)

            # Log execution event
            if exec_result.get("status") in ("executed", "dry_run"):
                log_event(
                    "trading",
                    f"order_{action.action.lower()}",
                    f"${ticker}: {action.action} {exec_result.get('qty', 0)} shares "
                    f"@ ${exec_result.get('price', 0):.2f} ({exec_result['status']})",
                    ticker=ticker,
                    metadata=exec_result,
                )
        else:
            result["exec_status"] = "hold"

        return result

    async def _build_context(self, ticker: str, portfolio: dict) -> dict:
        """Build the context dict for TradingAgent from precomputed data."""
        context: dict = {"symbol": ticker}

        log_event(
            "trading",
            "building_context",
            f"${ticker}: Gathering data for analysis...",
            ticker=ticker,
        )

        # ── Price data from yfinance ──────────────────────────
        try:
            t = yf.Ticker(ticker)
            fi = t.fast_info
            context["last_price"] = fi.get("lastPrice", 0) or 0
            context["today_change_pct"] = (
                ((fi.get("lastPrice", 0) / fi.get("previousClose", 1)) - 1) * 100
                if fi.get("previousClose")
                else 0
            )
            context["volume"] = fi.get("lastVolume", 0) or 0
            context["avg_volume"] = fi.get("threeMonthAverageVolume", 0) or 0
        except Exception as exc:
            logger.warning("[TradingPipeline] Price fetch failed for %s: %s", ticker, exc)
            context.update({"last_price": 0, "today_change_pct": 0, "volume": 0, "avg_volume": 0})

        log_event(
            "trading",
            "fetching_technicals",
            f"${ticker}: Loading technical indicators and ATR risk metrics",
            ticker=ticker,
            metadata={"price": context["last_price"]},
        )

        # ── ATR from technicals (for risk rules) ──────────────
        try:
            from app.database import get_db

            db = get_db()
            row = db.execute(
                "SELECT atr FROM technicals WHERE ticker = ? ORDER BY date DESC LIMIT 1",
                [ticker],
            ).fetchone()
            context["atr"] = float(row[0]) if row and row[0] else 0.0
        except Exception:
            context["atr"] = 0.0

        log_event(
            "trading",
            "loading_dossier",
            f"${ticker}: Executing deep analysis suite",
            ticker=ticker,
        )

        # ── Dossier context (precomputed by deep analysis) ────
        dossier = DeepAnalysisService.get_latest_dossier(ticker)
        if dossier:
            # Build technical summary from scorecard
            sc = dossier.get("scorecard", {})
            tech_parts = []
            if sc.get("rsi"):
                tech_parts.append(f"RSI={sc['rsi']:.0f}")
            if sc.get("macd"):
                tech_parts.append(f"MACD={sc['macd']:.2f}")
            if sc.get("sma_20"):
                tech_parts.append(f"SMA20=${sc['sma_20']:.2f}")
            if sc.get("sma_50"):
                tech_parts.append(f"SMA50=${sc['sma_50']:.2f}")
            if sc.get("bb_pct_b") is not None:
                tech_parts.append(f"BB%B={sc['bb_pct_b']:.2f}")
            context["technical_summary"] = " | ".join(tech_parts) if tech_parts else ""

            # Quant summary
            quant_parts = []
            if dossier.get("conviction_score"):
                quant_parts.append(f"Conviction: {dossier['conviction_score']:.0%}")
            if sc.get("kelly_fraction"):
                quant_parts.append(f"Kelly: {sc['kelly_fraction']:.1%}")
            if sc.get("sharpe_ratio"):
                quant_parts.append(f"Sharpe: {sc['sharpe_ratio']:.2f}")
            context["quant_summary"] = " | ".join(quant_parts) if quant_parts else ""

            # Analysis digest (chart, fundamentals, risk — no longer truncated;
            # context budget guard in TradingAgent handles overflow)
            parts = []
            if dossier.get("executive_summary"):
                parts.append(f"CHART ANALYSIS:\n{dossier['executive_summary']}")
            if dossier.get("bull_case"):
                parts.append(f"FUNDAMENTALS:\n{dossier['bull_case']}")
            if dossier.get("bear_case"):
                parts.append(f"RISK PROFILE:\n{dossier['bear_case']}")
            context["news_summary"] = "\n\n".join(parts) if parts else ""

            # Dossier conviction + signal for the trading agent
            context["dossier_conviction"] = dossier.get("conviction_score", 0)
            dossier_sig = (
                "BUY"
                if context["dossier_conviction"] >= 0.7
                else "SELL"
                if context["dossier_conviction"] <= 0.3
                else "HOLD"
            )
            context["dossier_signal"] = dossier_sig

            # Extract quant flags for risk override rules
            sc_flags = sc.get("flags", [])
            if isinstance(sc_flags, str):
                import json as _json

                try:
                    sc_flags = _json.loads(sc_flags)
                except Exception:
                    sc_flags = []
            context["quant_flags"] = sc_flags if isinstance(sc_flags, list) else []
        else:
            context["technical_summary"] = ""
            context["quant_summary"] = ""
            context["news_summary"] = ""
            context["dossier_conviction"] = 0
            context["dossier_signal"] = "UNKNOWN"
            context["quant_flags"] = []

        log_event(
            "trading",
            "rag_retrieval",
            f"${ticker}: Querying local vector embeddings for catalysts",
            ticker=ticker,
        )

        # ── RAG context (retrieved from embedded data) ────────
        try:
            from app.config import settings as _cfg

            if getattr(_cfg, "RAG_ENABLED", True):
                from app.services.retrieval_service import RetrievalService

                rag_svc = RetrievalService()
                cached_vec = self._query_vector_cache.get(ticker)
                if cached_vec:
                    logger.info(
                        "[TradingPipeline] RAG: using cached vector for %s",
                        ticker,
                    )
                else:
                    logger.info(
                        "[TradingPipeline] RAG: no cached vector for %s, attempting live embed",
                        ticker,
                    )
                context["rag_context"] = await rag_svc.retrieve_for_trading(
                    ticker,
                    query_vector=cached_vec,
                )
                if context["rag_context"]:
                    logger.info(
                        "[TradingPipeline] RAG: injected %d chars for %s",
                        len(context["rag_context"]),
                        ticker,
                    )
                else:
                    logger.info(
                        "[TradingPipeline] RAG: no relevant context for %s",
                        ticker,
                    )
            else:
                context["rag_context"] = ""
        except Exception as exc:
            logger.warning(
                "[TradingPipeline] RAG retrieval failed for %s: %s",
                ticker,
                exc,
            )
            context["rag_context"] = ""

        # ── Delta indicators from last decision ──────────────
        # Shows the LLM what changed since it last analyzed this ticker,
        # breaking the HOLD stagnation loop.
        log_event(
            "trading",
            "delta_analysis",
            f"${ticker}: Analyzing price action since previous decision",
            ticker=ticker,
        )
        try:
            from app.database import get_db as _get_db

            _db = _get_db()
            last_dec = _db.execute(
                "SELECT action, confidence, ts, rationale "
                "FROM trade_decisions "
                "WHERE symbol = ? AND bot_id = ? "
                "ORDER BY ts DESC LIMIT 1",
                [ticker, self._bot_id or "default"],
            ).fetchone()
            if last_dec and context.get("last_price"):
                # Get the price at the time of last decision
                last_action, last_conf, last_ts, last_rationale = last_dec
                last_price_row = _db.execute(
                    "SELECT close FROM technicals "
                    "WHERE ticker = ? AND date <= ? "
                    "ORDER BY date DESC LIMIT 1",
                    [ticker, last_ts],
                ).fetchone()
                last_price_at_decision = float(last_price_row[0]) if last_price_row and last_price_row[0] else 0
                current_price = context["last_price"]
                if last_price_at_decision > 0:
                    price_delta_pct = ((current_price / last_price_at_decision) - 1) * 100
                    context["delta_since_last"] = (
                        f"Last decision: {last_action} @ ${last_price_at_decision:.2f} "
                        f"(confidence={last_conf:.0%}) on {str(last_ts)[:10]}\n"
                        f"Price since: {price_delta_pct:+.2f}% "
                        f"(${last_price_at_decision:.2f} → ${current_price:.2f})"
                    )
                else:
                    context["delta_since_last"] = (
                        f"Last decision: {last_action} (confidence={last_conf:.0%}) "
                        f"on {str(last_ts)[:10]}"
                    )
            else:
                context["delta_since_last"] = ""
        except Exception as exc:
            logger.debug("[TradingPipeline] Delta indicators failed for %s: %s", ticker, exc)
            context["delta_since_last"] = ""

        log_event(
            "trading",
            "youtube_intel",
            f"${ticker}: Scanning fresh YouTube trader sentiment",
            ticker=ticker,
        )

        # ── YouTube catalyst intelligence ─────────────────────
        # Inject recent YouTube trading data that RAG may have missed.
        try:
            from app.database import get_db as _get_db2

            _db2 = _get_db2()
            yt_rows = _db2.execute(
                "SELECT title, channel, trading_data "
                "FROM youtube_trading_data "
                "WHERE ticker = ? "
                "ORDER BY collected_at DESC LIMIT 3",
                [ticker],
            ).fetchall()
            if yt_rows:
                yt_parts = []
                for yt in yt_rows:
                    title, channel, data = yt
                    snippet = f"[{channel}] {title}"
                    if data and len(str(data)) > 10:
                        snippet += f"\n{str(data)[:500]}"
                    yt_parts.append(snippet)
                context["youtube_intel"] = "\n\n".join(yt_parts)
            else:
                context["youtube_intel"] = ""
        except Exception as exc:
            logger.debug("[TradingPipeline] YouTube intel failed for %s: %s", ticker, exc)
            context["youtube_intel"] = ""

        log_event(
            "trading",
            "portfolio_context",
            f"${ticker}: Building sector breakdown and portfolio exposure",
            ticker=ticker,
        )

        # ── Portfolio context ─────────────────────────────────
        context["portfolio_cash"] = portfolio.get("cash_balance", 0)
        context["portfolio_value"] = portfolio.get("total_portfolio_value", 0)
        context["max_position_pct"] = 15
        context["all_positions"] = portfolio.get("positions", [])

        # ── Existing position ─────────────────────────────────
        positions = portfolio.get("positions", [])
        for p in positions:
            if p.get("ticker") == ticker:
                price = context.get("last_price", 0)
                entry = p.get("avg_entry_price", 0)
                qty = p.get("qty", 0)
                context["existing_position"] = {
                    "qty": qty,
                    "avg_entry": entry,
                    "unrealized_pnl": round((price - entry) * qty, 2) if price and entry else 0,
                }
                break

        # ── Sector Breakdown for Risk Management ──────────────
        try:
            from app.database import get_db
            db = get_db()
            
            # Target ticker sector
            row = db.execute(
                "SELECT sector FROM fundamentals WHERE ticker = ? ORDER BY snapshot_date DESC LIMIT 1",
                [ticker]
            ).fetchone()
            context["target_sector"] = row[0] if row and row[0] else "Unknown"
            
            # Sector weighting for current positions
            sector_counts = {}
            if context["all_positions"]:
                held_tickers = [p["ticker"] for p in context["all_positions"]]
                placeholders = ",".join("?" for _ in held_tickers)
                rows = db.execute(
                    f"SELECT ticker, MAX(sector) FROM fundamentals WHERE ticker IN ({placeholders}) GROUP BY ticker",
                    held_tickers
                ).fetchall()
                ticker_to_sector = {r[0]: (r[1] or "Unknown") for r in rows}
                
                for p in context["all_positions"]:
                    pticker = p["ticker"]
                    pqty = p.get("qty", 0)
                    pentry = p.get("avg_entry_price", 0)
                    pval = pqty * pentry
                    s = ticker_to_sector.get(pticker, "Unknown")
                    sector_counts[s] = sector_counts.get(s, 0) + pval
            
            context["sector_breakdown"] = sector_counts
        except Exception as exc:
            logger.warning("[TradingPipeline] Failed to build sector breakdown for %s: %s", ticker, exc)
            if "target_sector" not in context:
                context["target_sector"] = "Unknown"
            context["sector_breakdown"] = {}

        log_event(
            "trading",
            "context_complete",
            f"${ticker}: Full deep-dive analysis complete; routing to agent...",
            ticker=ticker,
        )
        return context
