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
from app.utils.logger import logger


class TradingPipelineService:
    """One-shot trading pipeline: symbols → decisions → execution."""

    def __init__(
        self,
        paper_trader: PaperTrader,
        *,
        dry_run: bool = True,
        bot_id: str = "default",
    ) -> None:
        self._trader = paper_trader
        self._agent = TradingAgent()
        self._executor = ExecutionService(paper_trader)
        self._deep = DeepAnalysisService()
        self._artifacts = ArtifactLogger()
        self._dry_run = dry_run
        self._bot_id = bot_id

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
        portfolio = self._trader.get_portfolio()
        results = []

        for ticker in valid_tickers:
            try:
                result = await self._process_ticker(ticker, portfolio, cycle_dir, cycle_id)
                results.append(result)
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
        context = await self._build_context(ticker, portfolio)

        # Save context artifact
        if cycle_dir:
            with contextlib.suppress(Exception):
                self._artifacts.save_context(cycle_dir, ticker, context)

        # ── Get LLM decision ──────────────────────────────────
        action, raw_llm = await self._agent.decide(context, self._bot_id)

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

            # News/analysis digest
            parts = []
            if dossier.get("executive_summary"):
                parts.append(dossier["executive_summary"][:300])
            if dossier.get("bull_case"):
                parts.append(f"BULL: {dossier['bull_case'][:150]}")
            if dossier.get("bear_case"):
                parts.append(f"BEAR: {dossier['bear_case'][:150]}")
            context["news_summary"] = "\n".join(parts) if parts else ""

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
        else:
            context["technical_summary"] = ""
            context["quant_summary"] = ""
            context["news_summary"] = ""
            context["dossier_conviction"] = 0
            context["dossier_signal"] = "UNKNOWN"

        # ── Portfolio context ─────────────────────────────────
        context["portfolio_cash"] = portfolio.get("cash_balance", 0)
        context["portfolio_value"] = portfolio.get("total_portfolio_value", 0)
        context["max_position_pct"] = 15

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

        return context
