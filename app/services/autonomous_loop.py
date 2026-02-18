"""Autonomous Loop — one-call orchestrator for the full trading bot pipeline.

Chains:  Discovery → Auto-Import → Deep Analysis → Trading
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from app.engine.signal_router import SignalRouter
from app.services.deep_analysis_service import DeepAnalysisService
from app.services.discovery_service import DiscoveryService
from app.services.paper_trader import PaperTrader
from app.services.price_monitor import PriceMonitor
from app.services.watchlist_manager import WatchlistManager
from app.utils.logger import logger


class AutonomousLoop:
    """Run every phase of the bot in one call."""

    def __init__(self) -> None:
        self.discovery = DiscoveryService()
        self.watchlist = WatchlistManager()
        self.deep_analysis = DeepAnalysisService()
        self.signal_router = SignalRouter()
        self.paper_trader = PaperTrader()
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
        logger.info("=" * 60)
        logger.info("[AutoLoop] ▶ Starting full autonomous loop")
        logger.info("=" * 60)

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

        logger.info("[AutoLoop] ✓ Full loop completed in %.1fs", elapsed)
        return report

    # ------------------------------------------------------------------
    # Phase implementations
    # ------------------------------------------------------------------

    async def _do_discovery(self) -> dict:
        """Step 1: Run Reddit + YouTube discovery."""
        result = await self.discovery.run_discovery(
            enable_reddit=True,
            enable_youtube=True,
            youtube_hours=24,
        )
        ticker_count = len(result.tickers) if result.tickers else 0
        self._log(f"Discovery found {ticker_count} tickers")
        return {
            "tickers_found": ticker_count,
            "tickers": [
                {"ticker": t.ticker, "score": t.total_score}
                for t in (result.tickers or [])[:10]
            ],
        }

    async def _do_import(self) -> dict:
        """Step 2: Import top discovery tickers to watchlist."""
        result = self.watchlist.import_from_discovery(min_score=3.0, max_tickers=10)
        self._log(
            f"Imported {result['total_imported']} tickers "
            f"(skipped {len(result.get('skipped', []))})"
        )
        return result

    async def _do_deep_analysis(self) -> dict:
        """Step 3: Run 4-layer analysis on every active watchlist ticker."""
        tickers = self.watchlist.get_active_tickers()
        if not tickers:
            self._log("No active tickers to analyze")
            return {"analyzed": 0, "tickers": []}

        self._log(f"Analyzing {len(tickers)} tickers: {', '.join(tickers)}")
        dossiers = await self.deep_analysis.analyze_batch(tickers, concurrency=2)

        summaries = []
        for d in dossiers:
            summaries.append({
                "ticker": d.ticker,
                "conviction": d.conviction_score,
                "signal": (
                    "BUY" if d.conviction_score >= 0.7
                    else "SELL" if d.conviction_score <= 0.3
                    else "HOLD"
                ),
            })

        self._log(
            f"Analysis complete: {len(dossiers)}/{len(tickers)} succeeded"
        )
        return {
            "analyzed": len(dossiers),
            "total": len(tickers),
            "results": summaries,
        }

    async def _do_trading(self) -> dict:
        """Step 4: Route dossier signals through paper trader."""
        tickers = self.watchlist.get_active_tickers()
        if not tickers:
            self._log("No active tickers for trading")
            return {"orders": 0, "tickers": []}

        orders_placed = []
        orders_today = self.paper_trader.get_orders_today_count()
        daily_pnl = self.paper_trader.get_daily_pnl_pct()
        portfolio = self.paper_trader.get_portfolio()

        # ---- Check triggers first ----
        triggered = await self.price_monitor.check_triggers()
        if triggered:
            self._log(f"{len(triggered)} price triggers fired")

        # ---- Process each ticker through signal router ----
        for ticker in tickers:
            try:
                dossier_data = DeepAnalysisService.get_latest_dossier(ticker)
                if not dossier_data:
                    continue

                conviction = dossier_data.get("conviction_score", 0.5)

                # Get current price
                from app.main import _fetch_one_quote
                quote = _fetch_one_quote(ticker)
                current_price = quote.get("price") if quote else None
                if not current_price:
                    self._log(f"{ticker}: no price available, skipping")
                    continue

                # Check existing position
                positions = portfolio.get("positions", [])
                existing_qty = 0
                for p in positions:
                    if p["ticker"] == ticker:
                        existing_qty = p["qty"]
                        break

                last_sold = self.paper_trader.get_last_sell_date(ticker)

                # Route the signal
                action = self.signal_router.evaluate(
                    ticker=ticker,
                    conviction_score=conviction,
                    current_price=current_price,
                    cash_balance=portfolio["cash_balance"],
                    total_portfolio_value=portfolio["total_portfolio_value"],
                    existing_position_qty=existing_qty,
                    orders_today=orders_today,
                    daily_pnl_pct=daily_pnl,
                    last_sold_date=last_sold,
                )

                if not action:
                    continue

                # Execute the order
                if action["side"] == "buy":
                    order = self.paper_trader.buy(
                        ticker=action["ticker"],
                        qty=action["qty"],
                        price=action["price"],
                        conviction_score=action["conviction"],
                        signal=action["signal"],
                    )
                    if order:
                        # Set triggers for new position
                        self.paper_trader.set_triggers_for_position(
                            ticker=ticker,
                            entry_price=action["price"],
                            qty=action["qty"],
                        )
                else:
                    order = self.paper_trader.sell(
                        ticker=action["ticker"],
                        qty=action["qty"],
                        price=action["price"],
                        conviction_score=action["conviction"],
                        signal=action["signal"],
                    )

                if order:
                    orders_placed.append({
                        "ticker": ticker,
                        "side": order.side,
                        "qty": order.qty,
                        "price": order.price,
                    })
                    orders_today += 1
                    # Refresh portfolio for next iteration
                    portfolio = self.paper_trader.get_portfolio()

            except Exception as exc:
                self._log(f"{ticker} trading error: {exc}")
                logger.error("[AutoLoop] Trading error for %s: %s", ticker, exc, exc_info=True)

        # ---- Take portfolio snapshot ----
        self.paper_trader.take_snapshot()

        self._log(
            f"Trading complete: {len(orders_placed)} orders placed, "
            f"{len(triggered)} triggers fired"
        )
        return {
            "orders_placed": len(orders_placed),
            "triggers_fired": len(triggered),
            "orders": orders_placed,
            "portfolio_value": portfolio["total_portfolio_value"],
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
            logger.info(
                "[AutoLoop] Phase %s completed in %.1fs", phase_name, elapsed
            )
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
