"""Autonomous Loop — one-call orchestrator for the full trading bot pipeline.

Chains:  Discovery → Auto-Import → Data Collection → Deep Analysis → Trading
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from app.services.deep_analysis_service import DeepAnalysisService
from app.services.discovery_service import DiscoveryService
from app.services.event_logger import end_loop, log_event, start_loop
from app.services.paper_trader import PaperTrader
from app.services.pipeline_service import PipelineService
from app.services.price_monitor import PriceMonitor
from app.services.watchlist_manager import WatchlistManager
from app.utils.logger import logger


class AutonomousLoop:
    """Run every phase of the bot in one call."""

    def __init__(self, *, max_tickers: int = 10) -> None:
        self.discovery = DiscoveryService()
        self.watchlist = WatchlistManager()
        self.paper_trader = PaperTrader()
        self.deep_analysis = DeepAnalysisService()
        self.max_tickers = max_tickers  # Cap discovery results for faster runs
        self.price_monitor = PriceMonitor(self.paper_trader)

        # Live state the frontend can poll
        self._state: dict[str, Any] = {
            "running": False,
            "phase": None,
            "phases": {},
            "started_at": None,
            "log": [],
        }

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return current loop state (for polling)."""
        return dict(self._state)

    async def run_full_loop(self) -> dict:
        """Execute the complete autonomous pipeline.

        Returns a summary dict with per-phase results and total timing.
        """
        if self._state["running"]:
            return {"error": "Loop is already running"}

        self._reset_state()
        t0 = time.time()
        loop_id = start_loop()
        logger.info("=" * 60)
        logger.info("[AutoLoop] ▶ Starting full autonomous loop (%s)", loop_id)
        logger.info("=" * 60)
        log_event("system", "loop_start", "Full autonomous loop started")

        report: dict[str, Any] = {
            "started_at": datetime.now().isoformat(),
            "phases": {},
        }

        # ── Step 1: Discovery ─────────────────────────────────────
        discovery_result = await self._run_phase(
            "discovery",
            "Scanning Reddit + YouTube for tickers…",
            self._do_discovery,
        )
        report["phases"]["discovery"] = discovery_result

        # ── Step 2: Auto-Import ───────────────────────────────────
        import_result = await self._run_phase(
            "import",
            "Importing top tickers to watchlist…",
            self._do_import,
        )
        report["phases"]["import"] = import_result

        # ── Step 2.5: Data Collection (all active tickers) ─────────
        collection_result = await self._run_phase(
            "collection",
            "Collecting financial data for all active tickers…",
            self._do_collection,
        )
        report["phases"]["collection"] = collection_result

        # ── Step 3: Deep Analysis (all active tickers) ────────────
        analysis_result = await self._run_phase(
            "analysis",
            "Running 4-layer deep analysis on all active tickers…",
            self._do_deep_analysis,
        )
        report["phases"]["analysis"] = analysis_result

        # ── Step 4: Trading (Signal Router + Paper Trader) ─────────
        trading_result = await self._run_phase(
            "trading",
            "Processing signals through paper trader…",
            self._do_trading,
        )
        report["phases"]["trading"] = trading_result

        # ── Done ──────────────────────────────────────────────────
        elapsed = round(time.time() - t0, 1)
        report["total_seconds"] = elapsed
        report["completed_at"] = datetime.now().isoformat()

        self._state["running"] = False
        self._state["phase"] = "done"
        self._log(f"Full loop completed in {elapsed}s")

        log_event(
            "system",
            "loop_complete",
            f"Full loop completed in {elapsed}s",
            metadata={"total_seconds": elapsed},
        )
        end_loop()

        logger.info("[AutoLoop] ✓ Full loop completed in %.1fs", elapsed)
        return report

    # ------------------------------------------------------------------
    # Phase implementations
    # ------------------------------------------------------------------

    async def _do_discovery(self) -> dict:
        """Step 1: Run Reddit + YouTube discovery."""
        log_event("discovery", "discovery_scan_start", "Starting Reddit + YouTube scan")
        result = await self.discovery.run_discovery(
            enable_reddit=True,
            enable_youtube=True,
            youtube_hours=24,
            max_tickers=self.max_tickers,
        )
        ticker_count = len(result.tickers) if result.tickers else 0
        self._log(f"Discovery found {ticker_count} tickers")
        log_event(
            "discovery",
            "discovery_scan_complete",
            f"Discovery complete: {ticker_count} tickers, "
            f"{result.transcript_count} transcripts",
            metadata={
                "tickers": ticker_count,
                "reddit_count": result.reddit_count,
                "youtube_count": result.youtube_count,
                "transcripts": result.transcript_count,
                "duration_s": result.duration_seconds,
            },
        )
        return {
            "tickers_found": ticker_count,
            "tickers": [
                {"ticker": t.ticker, "score": t.discovery_score}
                for t in (result.tickers or [])[:10]
            ],
        }

    async def _do_import(self) -> dict:
        """Step 2: Import top discovery tickers to watchlist."""
        result = self.watchlist.import_from_discovery(min_score=3.0, max_tickers=10)
        imported = result.get("total_imported", 0)
        skipped = len(result.get("skipped", []))
        self._log(f"Imported {imported} tickers (skipped {skipped})")
        for t in result.get("imported", []):
            # imported is a list of ticker strings, not dicts
            log_event(
                "import",
                "watchlist_import",
                f"${t} auto-imported from discovery",
                ticker=t,
                metadata={"source": "auto_discovery"},
            )
        if imported == 0:
            log_event(
                "import",
                "watchlist_import",
                f"No new tickers imported ({skipped} skipped)",
                status="skipped",
            )
        return result

    async def _do_collection(self) -> dict:
        """Step 2.5: Collect financial data for active watchlist tickers."""
        tickers = self.watchlist.get_active_tickers()
        if not tickers:
            self._log("No active tickers to collect data for")
            log_event(
                "collection",
                "collection_skip",
                "No active tickers to collect data for",
                status="skipped",
            )
            return {"collected": 0, "tickers": []}

        self._log(f"Collecting data for {len(tickers)} tickers: {', '.join(tickers)}")
        log_event(
            "collection",
            "collection_batch_start",
            f"Starting data collection for {len(tickers)} tickers",
            metadata={"tickers": tickers, "count": len(tickers)},
        )

        import asyncio

        sem = asyncio.Semaphore(3)  # Up to 3 tickers concurrently

        async def _collect_one(ticker: str) -> str | None:
            async with sem:
                try:
                    pipeline = PipelineService()
                    await pipeline.run(ticker, mode="data")
                    log_event(
                        "collection",
                        "collection_ticker_done",
                        f"${ticker}: data collection complete",
                        ticker=ticker,
                    )
                    return ticker
                except Exception as exc:
                    logger.warning("[AutoLoop] Collection failed for %s: %s", ticker, exc)
                    log_event(
                        "collection",
                        "collection_ticker_error",
                        f"${ticker}: data collection failed — {exc}",
                        ticker=ticker,
                        status="error",
                    )
                    return None

        results = await asyncio.gather(*[_collect_one(t) for t in tickers])
        succeeded = [t for t in results if t is not None]

        self._log(f"Collection complete: {len(succeeded)}/{len(tickers)} succeeded")
        log_event(
            "collection",
            "collection_batch_complete",
            f"Data collection complete: {len(succeeded)}/{len(tickers)} succeeded",
            metadata={"succeeded": len(succeeded), "total": len(tickers)},
        )
        return {"collected": len(succeeded), "total": len(tickers), "tickers": succeeded}

    async def _do_deep_analysis(self) -> dict:
        """Step 3: Run 4-layer analysis on every active watchlist ticker."""
        tickers = self.watchlist.get_active_tickers()
        if not tickers:
            self._log("No active tickers to analyze")
            log_event(
                "analysis",
                "analysis_skip",
                "No active tickers to analyze",
                status="skipped",
            )
            return {"analyzed": 0, "tickers": []}

        # Build portfolio context for the LLM synthesis
        portfolio = self.paper_trader.get_portfolio()
        portfolio_context = {
            "cash_balance": portfolio["cash_balance"],
            "total_portfolio_value": portfolio["total_portfolio_value"],
            "positions": {
                p["ticker"]: {
                    "qty": p["qty"],
                    "avg_entry": p["avg_entry_price"],
                    "cost_basis": p["qty"] * p["avg_entry_price"],
                }
                for p in portfolio.get("positions", [])
            },
            "realized_pnl": portfolio.get("realized_pnl", 0.0),
        }

        self._log(f"Analyzing {len(tickers)} tickers: {', '.join(tickers)}")
        log_event(
            "analysis",
            "analysis_batch_start",
            f"Starting analysis for {len(tickers)} tickers: {', '.join(tickers)}",
            metadata={"tickers": tickers, "count": len(tickers)},
        )
        dossiers = await self.deep_analysis.analyze_batch(
            tickers, concurrency=2, portfolio_context=portfolio_context,
        )

        summaries = []
        for d in dossiers:
            signal = (
                "BUY"
                if d.conviction_score >= 0.7
                else "SELL"
                if d.conviction_score <= 0.3
                else "HOLD"
            )
            summaries.append(
                {
                    "ticker": d.ticker,
                    "conviction": d.conviction_score,
                    "signal": signal,
                }
            )
            log_event(
                "analysis",
                "dossier_synthesized",
                f"${d.ticker}: dossier generated — conviction {d.conviction_score:.0%} {signal}",
                ticker=d.ticker,
                metadata={"conviction": d.conviction_score, "signal": signal},
            )

        self._log(f"Analysis complete: {len(dossiers)}/{len(tickers)} succeeded")
        log_event(
            "analysis",
            "analysis_batch_complete",
            f"Analysis complete: {len(dossiers)}/{len(tickers)} succeeded",
            metadata={"succeeded": len(dossiers), "total": len(tickers)},
        )
        return {
            "analyzed": len(dossiers),
            "total": len(tickers),
            "results": summaries,
        }

    async def _do_trading(self) -> dict:
        """Step 4: LLM Portfolio Strategist makes all trading decisions.

        Instead of processing each ticker through hardcoded SignalRouter
        thresholds, we feed ALL dossiers to the Portfolio Strategist LLM.
        The LLM compares stocks, decides allocation, and executes trades
        via tool-calling.
        """
        tickers = self.watchlist.get_active_tickers()
        if not tickers:
            self._log("No active tickers for trading")
            log_event(
                "trading",
                "trading_skip",
                "No active tickers for trading",
                status="skipped",
            )
            return {"orders": 0, "tickers": []}

        # ---- Check price triggers first ----
        triggered = await self.price_monitor.check_triggers()
        if triggered:
            self._log(f"{len(triggered)} price triggers fired")
            for trig in triggered:
                log_event(
                    "trading",
                    "trigger_fired",
                    f"${trig.get('ticker', '?')}: "
                    f"{trig.get('trigger_type', '?')} triggered",
                    ticker=trig.get("ticker"),
                    metadata=trig,
                )

        # ---- Run Portfolio Strategist (LLM tool-calling) ----
        self._log(
            f"Portfolio Strategist analyzing {len(tickers)} tickers "
            f"for trading decisions…"
        )

        from app.engine.portfolio_strategist import PortfolioStrategist
        from app.engine.strategist_audit import StrategistAudit

        audit = StrategistAudit()
        strategist = PortfolioStrategist(
            paper_trader=self.paper_trader,
            tickers=tickers,
            audit=audit,
        )

        try:
            result = await strategist.run()
        except Exception as exc:
            logger.exception("[AutoLoop] Portfolio Strategist failed")
            self._log(f"Strategist error: {exc}")
            log_event(
                "trading",
                "strategist_error",
                f"Portfolio Strategist failed: {exc}",
                status="error",
            )
            return {"orders": 0, "error": str(exc)}

        # ---- Log results ----
        orders_count = result.get("orders_placed", 0)
        triggers_count = result.get("triggers_set", 0)
        summary = result.get("summary", "")
        audit_path = result.get("audit_report", "")

        self._log(
            f"Strategist: {orders_count} orders, "
            f"{triggers_count} triggers. {summary[:100]}"
        )
        if audit_path:
            self._log(f"Audit report: {audit_path}")

        log_event(
            "trading",
            "strategist_complete",
            f"Portfolio Strategist: {orders_count} orders, "
            f"{triggers_count} triggers set",
            metadata={
                "orders_placed": orders_count,
                "triggers_set": triggers_count,
                "turns_used": result.get("turns_used", 0),
                "summary": summary,
                "audit_report": audit_path,
            },
        )

        # Log individual orders for activity feed
        for order in result.get("orders", []):
            side = order.get("side", "?").upper()
            ticker = order.get("ticker", "?")
            qty = order.get("qty", 0)
            price = order.get("price", 0)
            reason = order.get("reason", "")
            log_event(
                "trading",
                f"order_{side.lower()}",
                f"${ticker}: {side} {qty} shares @ ${price:.2f} — {reason}",
                ticker=ticker,
                metadata=order,
            )

        return {
            "orders": orders_count,
            "triggers": triggers_count,
            "summary": summary,
            "tickers": tickers,
            "audit_report": audit_path,
        }



    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _run_phase(
        self,
        phase_name: str,
        description: str,
        coro_fn: Any,
    ) -> dict:
        """Execute a phase with timing, error handling, and state updates."""
        self._state["phase"] = phase_name
        self._state["phases"][phase_name] = "running"
        self._log(description)
        logger.info("[AutoLoop] Phase: %s — %s", phase_name, description)

        t0 = time.time()
        try:
            result = await coro_fn()
            elapsed = round(time.time() - t0, 1)
            result["duration_seconds"] = elapsed
            result["status"] = "success"
            self._state["phases"][phase_name] = "done"
            logger.info("[AutoLoop] Phase %s completed in %.1fs", phase_name, elapsed)
            return result
        except Exception as exc:
            elapsed = round(time.time() - t0, 1)
            self._state["phases"][phase_name] = "error"
            error_msg = f"{phase_name} failed: {exc}"
            self._log(error_msg)
            logger.error("[AutoLoop] %s", error_msg, exc_info=True)
            return {
                "status": "error",
                "error": str(exc),
                "duration_seconds": elapsed,
            }

    def _log(self, msg: str) -> None:
        """Append a timestamped message to the live log."""
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "message": msg,
        }
        self._state["log"].append(entry)

    def _reset_state(self) -> None:
        self._state = {
            "running": True,
            "phase": "starting",
            "phases": {},
            "started_at": datetime.now().isoformat(),
            "log": [],
        }
