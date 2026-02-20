"""Trading Scheduler — APScheduler-based daily automation.

Wraps the existing AutonomousLoop in a time-aware schedule:
  - Pre-market (6:00 AM ET): Full discovery → analysis → trading loop
  - Market hours (every 60s): Price trigger monitoring
  - Midday (10:30, 12:30, 2:30 ET): Re-analysis of active tickers
  - End of day (4:30 PM ET): Portfolio snapshot + EOD report
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.database import get_db
from app.services.report_generator import ReportGenerator
from app.utils.logger import logger
from app.utils.market_hours import is_market_open, market_status

# Eastern timezone string for APScheduler
_ET = "America/New_York"


class TradingScheduler:
    """Manages the full daily automation schedule."""

    def __init__(
        self,
        autonomous_loop: object,
        price_monitor: object,
    ) -> None:
        self._loop = autonomous_loop
        self._monitor = price_monitor
        self._reports = ReportGenerator()
        self._scheduler: AsyncIOScheduler | None = None
        self.is_running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> dict:
        """Start the automated daily schedule."""
        if self.is_running:
            return {"status": "already_running"}

        self._scheduler = AsyncIOScheduler()

        # ── Pre-market: 6:00 AM ET weekdays ─────────────────────────
        self._scheduler.add_job(
            self._pre_market_run,
            CronTrigger(
                hour=6, minute=0, day_of_week="mon-fri", timezone=_ET,
            ),
            id="pre_market",
            name="Pre-Market Full Loop",
            replace_existing=True,
        )

        # ── Price monitoring: every 60s ─────────────────────────────
        self._scheduler.add_job(
            self._price_monitor_tick,
            IntervalTrigger(seconds=60),
            id="price_monitor",
            name="Price Monitor",
            replace_existing=True,
        )

        # ── Midday re-analysis: 10:30, 12:30, 2:30 ET ──────────────
        for hour in [10, 12, 14]:
            self._scheduler.add_job(
                self._midday_reanalysis,
                CronTrigger(
                    hour=hour, minute=30, day_of_week="mon-fri", timezone=_ET,
                ),
                id=f"midday_{hour}",
                name=f"Midday Re-Analysis ({hour}:30)",
                replace_existing=True,
            )

        # ── End of day: 4:30 PM ET weekdays ─────────────────────────
        self._scheduler.add_job(
            self._end_of_day_run,
            CronTrigger(
                hour=16, minute=30, day_of_week="mon-fri", timezone=_ET,
            ),
            id="end_of_day",
            name="End of Day Report",
            replace_existing=True,
        )

        self._scheduler.start()
        self.is_running = True
        logger.info("[Scheduler] Started — 6 jobs registered")
        return {"status": "started", "jobs": len(self._scheduler.get_jobs())}

    def stop(self) -> dict:
        """Stop all scheduled jobs."""
        if not self.is_running or not self._scheduler:
            return {"status": "not_running"}

        self._scheduler.shutdown(wait=False)
        self._scheduler = None
        self.is_running = False
        logger.info("[Scheduler] Stopped — all jobs removed")
        return {"status": "stopped"}

    # ------------------------------------------------------------------
    # Status & History
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return scheduler state for frontend display."""
        jobs = []
        if self._scheduler and self.is_running:
            for job in self._scheduler.get_jobs():
                next_run = job.next_run_time
                jobs.append({
                    "id": job.id,
                    "name": job.name,
                    "next_run": next_run.isoformat() if next_run else None,
                    "next_run_human": (
                        next_run.strftime("%I:%M %p ET")
                        if next_run else "—"
                    ),
                })

        return {
            "is_running": self.is_running,
            "jobs": jobs,
            "job_count": len(jobs),
            "market": market_status(),
        }

    @staticmethod
    def get_history(limit: int = 20) -> list[dict]:
        """Get recent scheduler run history from DB."""
        db = get_db()
        rows = db.execute(
            "SELECT id, job_name, started_at, completed_at, status, "
            "summary, error "
            "FROM scheduler_runs "
            "ORDER BY started_at DESC LIMIT ?",
            [limit],
        ).fetchall()
        return [
            {
                "id": r[0],
                "job_name": r[1],
                "started_at": str(r[2]) if r[2] else None,
                "completed_at": str(r[3]) if r[3] else None,
                "status": r[4],
                "summary": r[5],
                "error": r[6],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Manual triggers
    # ------------------------------------------------------------------

    async def run_job(self, job_name: str) -> dict:
        """Manually trigger a specific job by name."""
        handlers = {
            "pre_market": self._pre_market_run,
            "midday": self._midday_reanalysis,
            "end_of_day": self._end_of_day_run,
            "price_monitor": self._price_monitor_tick,
        }
        handler = handlers.get(job_name)
        if not handler:
            return {"error": f"Unknown job: {job_name}"}

        await handler()
        return {"status": "completed", "job": job_name}

    def add_one_shot_job(
        self, ticker: str, delay_minutes: int, reason: str,
    ) -> dict:
        """Schedule a one-shot re-analysis for a specific ticker."""
        if not self._scheduler or not self.is_running:
            return {"error": "Scheduler is not running"}

        fire_time = datetime.now() + timedelta(minutes=delay_minutes)
        job_id = f"wakeup_{ticker}_{fire_time.strftime('%H%M')}"

        self._scheduler.add_job(
            self._targeted_reanalysis,
            DateTrigger(run_date=fire_time),
            args=[ticker, reason],
            id=job_id,
            name=f"Wakeup: {ticker} ({reason[:40]})",
            replace_existing=True,
        )

        logger.info(
            "[Scheduler] Scheduled wakeup for %s at %s — %s",
            ticker, fire_time.strftime("%I:%M %p"), reason,
        )
        return {
            "status": "scheduled",
            "ticker": ticker,
            "fires_at": fire_time.strftime("%I:%M %p"),
            "reason": reason,
        }

    # ------------------------------------------------------------------
    # Job implementations
    # ------------------------------------------------------------------

    async def _pre_market_run(self) -> None:
        """6:00 AM ET — Full loop + pre-market report."""
        run_id = self._log_start("pre_market")
        try:
            logger.info("[Scheduler] === PRE-MARKET RUN STARTING ===")

            # Run the full loop
            await self._loop.run_full_loop()

            # Generate pre-market report
            loop_status = self._loop.get_status()
            report = self._reports.generate_pre_market(loop_status)

            orders_count = len(report.get("orders_today", []))
            summary = (
                f"Loop completed. "
                f"{len(report.get('discoveries', []))} discoveries, "
                f"{orders_count} orders placed."
            )
            self._log_end(run_id, "success", summary)
            logger.info("[Scheduler] === PRE-MARKET RUN COMPLETE: %s ===", summary)

        except Exception as e:
            self._log_end(run_id, "error", error=str(e))
            logger.exception("[Scheduler] Pre-market run failed")

    async def _price_monitor_tick(self) -> None:
        """Every 60s — check triggers (only during market hours)."""
        if not is_market_open():
            return  # Silently skip when market is closed

        try:
            actions = await self._monitor.check_triggers()
            if actions:
                logger.info(
                    "[Scheduler] Price monitor: %d triggers fired", len(actions),
                )
        except Exception:
            logger.exception("[Scheduler] Price monitor tick failed")

    async def _midday_reanalysis(self) -> None:
        """10:30 / 12:30 / 2:30 ET — Re-analyze active tickers."""
        if not is_market_open():
            return

        run_id = self._log_start("midday_reanalysis")
        try:
            logger.info("[Scheduler] === MIDDAY RE-ANALYSIS STARTING ===")

            # Run just the analysis + trading phases
            from app.services.autonomous_loop import AutonomousLoop

            loop = AutonomousLoop(max_tickers=self._loop.max_tickers)
            # Run analysis on existing watchlist (skip discovery + import)
            await loop._do_deep_analysis()
            await loop._do_trading()

            summary = "Midday re-analysis + trading complete."
            self._log_end(run_id, "success", summary)
            logger.info("[Scheduler] === MIDDAY RE-ANALYSIS COMPLETE ===")

        except Exception as e:
            self._log_end(run_id, "error", error=str(e))
            logger.exception("[Scheduler] Midday re-analysis failed")

    async def _targeted_reanalysis(self, ticker: str, reason: str) -> None:
        """Wakeup job — re-analyze and re-trade a single ticker."""
        run_id = self._log_start(f"wakeup_{ticker}")
        try:
            logger.info(
                "[Scheduler] === WAKEUP: %s — %s ===", ticker, reason,
            )
            from app.services.deep_analysis_service import DeepAnalysisService
            from app.engine.portfolio_strategist import PortfolioStrategist
            from app.services.paper_trader import PaperTrader

            # Re-analyze the single ticker
            deep = DeepAnalysisService()
            dossier = await deep.analyze_ticker(ticker)

            # Run strategist on just this ticker
            trader = PaperTrader()
            strategist = PortfolioStrategist(
                paper_trader=trader, tickers=[ticker],
            )
            result = await strategist.run()

            orders = result.get("orders_placed", 0)
            summary = (
                f"Wakeup for {ticker}: conviction={dossier.conviction_score:.2f}, "
                f"{orders} orders placed. Reason: {reason}"
            )
            self._log_end(run_id, "success", summary)
            logger.info("[Scheduler] === WAKEUP COMPLETE: %s ===", summary)

        except Exception as e:
            self._log_end(run_id, "error", error=str(e))
            logger.exception("[Scheduler] Wakeup for %s failed", ticker)

    async def _end_of_day_run(self) -> None:
        """4:30 PM ET — Portfolio snapshot + EOD report."""
        run_id = self._log_start("end_of_day")
        try:
            logger.info("[Scheduler] === END-OF-DAY RUN STARTING ===")

            # Take portfolio snapshot
            from app.services.paper_trader import PaperTrader

            trader = PaperTrader()
            trader.take_snapshot()

            # Generate EOD report (includes score decay)
            report = self._reports.generate_eod()

            positions = len(report.get("open_positions", []))
            orders = len(report.get("todays_orders", []))
            summary = (
                f"EOD complete. "
                f"{positions} open positions, "
                f"{orders} orders today."
            )
            self._log_end(run_id, "success", summary)
            logger.info("[Scheduler] === END-OF-DAY COMPLETE: %s ===", summary)

        except Exception as e:
            self._log_end(run_id, "error", error=str(e))
            logger.exception("[Scheduler] End-of-day run failed")

    # ------------------------------------------------------------------
    # DB logging helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _log_start(job_name: str) -> str:
        """Log job start to scheduler_runs table."""
        run_id = str(uuid.uuid4())[:8]
        db = get_db()
        db.execute(
            "INSERT INTO scheduler_runs (id, job_name, started_at, status) "
            "VALUES (?, ?, ?, 'running')",
            [run_id, job_name, datetime.utcnow()],
        )
        db.commit()
        return run_id

    @staticmethod
    def _log_end(
        run_id: str,
        status: str,
        summary: str = "",
        error: str = "",
    ) -> None:
        """Log job completion to scheduler_runs table."""
        db = get_db()
        db.execute(
            "UPDATE scheduler_runs "
            "SET completed_at = ?, status = ?, summary = ?, error = ? "
            "WHERE id = ?",
            [datetime.utcnow(), status, summary, error, run_id],
        )
        db.commit()
