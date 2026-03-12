"""Autonomous Loop — one-call orchestrator for the full trading bot pipeline.

Chains:  Discovery → Auto-Import → Data Collection → Deep Analysis → Trading
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from app.services.deep_analysis_service import DeepAnalysisService
from app.services.discovery_service import DiscoveryService
from app.services.event_logger import end_loop, log_event, set_bot_context, start_loop
from app.services.paper_trader import PaperTrader
from app.services.pipeline_health import (
    HealthTracker,
    clear_active_tracker,
    set_active_tracker,
)
from app.services.pipeline_service import PipelineService
from app.services.price_monitor import PriceMonitor
from app.services.watchlist_manager import WatchlistManager
from app.utils.logger import logger

# Tickers analyzed within this window are skipped by collection / analysis.
_ANALYSIS_CACHE_TTL = timedelta(hours=24)


class AutonomousLoop:
    """Run every phase of the bot in one call."""

    def __init__(self, *, max_tickers: int = 10, bot_id: str = "default", model_name: str = "") -> None:
        self.bot_id = bot_id
        self.model_name = model_name
        self.discovery = DiscoveryService()
        self.watchlist = WatchlistManager(bot_id=bot_id)
        self.paper_trader = PaperTrader(bot_id=bot_id)
        self.deep_analysis = DeepAnalysisService()
        self.max_tickers = max_tickers  # Cap discovery results for faster runs
        self.price_monitor = PriceMonitor(self.paper_trader)
        self._cancelled = False

        # Live state the frontend can poll
        self._state: dict[str, Any] = {
            "running": False,
            "phase": None,
            "phases": {},
            "started_at": None,
            "bot_id": bot_id,
            "model_name": model_name,
            "log": [],
        }

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return current loop state (for polling)."""
        status = dict(self._state)
        status["cancelled"] = self._cancelled
        return status

    def cancel(self) -> None:
        """Request graceful cancellation — loop stops after current phase."""
        self._cancelled = True
        self._state["running"] = False
        self._state["phase"] = "cancelled"
        self._log("⛔ Emergency stop requested — cancelling after current phase")
        logger.info("[AutoLoop] Cancel requested for bot=%s", self.bot_id)

    async def run_full_loop(self) -> dict:
        """Execute the complete autonomous pipeline.

        Returns a summary dict with per-phase results and total timing.
        """
        if self._state["running"]:
            return {"error": "Loop is already running"}

        self._reset_state()
        t0 = time.time()
        loop_id = start_loop()
        set_bot_context(self.bot_id, self.model_name)

        # ── Health tracker for this run ──
        self._health = HealthTracker(loop_id=loop_id)
        set_active_tracker(self._health)

        logger.info("=" * 60)
        logger.info("[AutoLoop] ▶ Starting full autonomous loop (%s)", loop_id)
        logger.info("=" * 60)
        log_event("system", "loop_start", "Full autonomous loop started")

        # ── Pre-warm the LLM model before pipeline starts ──
        # Ollama evicts models after 5 min idle. Data collection can take
        # 30+ min, so we pre-load the model with a 2-hour keep_alive
        # to ensure it stays in VRAM for the entire loop.
        from app.config import settings
        from app.services.llm_service import LLMService

        logger.info(
            "[AutoLoop] Pre-warming Ollama model: %s @ %s",
            settings.LLM_MODEL, settings.OLLAMA_URL,
        )
        warm_result = await LLMService.verify_and_warm_ollama_model(
            settings.OLLAMA_URL,
            settings.LLM_MODEL,
            keep_alive="2h",
        )
        if warm_result.get("status") == "oom_error":
            # OOM — apply the suggested (lower) context size
            sug_ctx = warm_result.get("suggested_ctx", 8192)
            settings.LLM_CONTEXT_SIZE = sug_ctx
            logger.warning(
                "[AutoLoop] ⚠️ OOM at ctx=%d — using suggested ctx=%d",
                warm_result.get("requested_ctx", 0), sug_ctx,
            )
            self._health.record_check(
                "LLM model pre-warmed",
                passed=True,
                detail=(
                    f"{settings.LLM_MODEL} OOM → ctx={sug_ctx} "
                    f"(suggested)"
                ),
            )
        elif warm_result.get("pre_warmed"):
            rec_ctx = warm_result.get("recommended_ctx", 32768)
            model_max = warm_result.get("model_max_ctx", 0)
            vram_bytes = warm_result.get("vram_bytes", 0)
            vram_gb = vram_bytes / (1024 ** 3) if vram_bytes else 0

            # Apply the VRAM-based cap to settings
            old_ctx = settings.LLM_CONTEXT_SIZE
            settings.LLM_CONTEXT_SIZE = min(old_ctx, rec_ctx)

            logger.info(
                "[AutoLoop] ✅ Model pre-warmed | "
                "VRAM=%.1fGB | model_max_ctx=%d | "
                "user_ctx=%d → effective_ctx=%d",
                vram_gb, model_max, old_ctx,
                settings.LLM_CONTEXT_SIZE,
            )
            self._log(
                f"✅ Pre-warmed {settings.LLM_MODEL} | "
                f"VRAM Used: {vram_gb:.1f}GB | "
                f"Max Ctx Loaded: {settings.LLM_CONTEXT_SIZE}"
            )
            self._health.record_check(
                "LLM model pre-warmed",
                passed=True,
                detail=(
                    f"{settings.LLM_MODEL} "
                    f"ctx={settings.LLM_CONTEXT_SIZE}"
                ),
            )
        else:
            logger.warning(
                "[AutoLoop] ⚠️ Model pre-warm failed: %s", warm_result,
            )
            self._health.record_check(
                "LLM model pre-warmed",
                passed=False,
                detail=warm_result.get("status", "unknown"),
            )

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

        # Health check: discovery found tickers?
        disc_count = discovery_result.get("tickers_found", 0)
        self._health.record_check(
            "Discovery found tickers",
            passed=disc_count > 0,
            detail=f"{disc_count} tickers" if disc_count else "0 tickers",
        )

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

        # ── Step 2.7: RAG Embedding (embed new data for retrieval) ──
        embedding_result = await self._run_phase(
            "embedding",
            "Embedding collected data for RAG retrieval…",
            self._do_embedding,
        )
        report["phases"]["embedding"] = embedding_result

        # Health check: embeddings generated?
        embed_chunks = embedding_result.get("total_chunks", 0)
        self._health.record_check(
            "RAG embeddings generated",
            passed=True,  # Not critical — zero is fine if nothing new
            detail=f"{embed_chunks} new chunks"
            if embed_chunks else "no new data to embed",
        )

        # ── Step 3: Deep Analysis (all active tickers) ────────────
        analysis_result = await self._run_phase(
            "analysis",
            "Running 4-layer deep analysis on all active tickers…",
            self._do_deep_analysis,
        )
        report["phases"]["analysis"] = analysis_result

        # Health check: dossiers generated?
        analyzed = analysis_result.get("analyzed", 0)
        total_tickers = analysis_result.get("total", 0)
        self._health.record_check(
            "Dossiers generated",
            passed=analyzed > 0,
            detail=f"{analyzed}/{total_tickers} tickers"
            if total_tickers else "no tickers to analyze",
        )

        # ── Step 4: Trading (Signal Router + Paper Trader) ─────────
        trading_result = await self._run_phase(
            "trading",
            "Processing signals through paper trader…",
            self._do_trading,
        )
        report["phases"]["trading"] = trading_result

        # Health check: strategist placed trades?
        orders_count = trading_result.get("orders", 0)
        self._health.record_check(
            "Strategist placed trades",
            passed=orders_count > 0,
            detail=f"{orders_count} orders" if orders_count else "0 orders",
        )

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

        # ── Generate health report ──
        try:
            health_path = self._health.generate_report()
            report["health_report"] = health_path
            logger.info("[AutoLoop] Health report: %s", health_path)
        except Exception as exc:
            logger.warning("[AutoLoop] Health report generation failed: %s", exc)
        finally:
            clear_active_tracker()

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
                "sec_13f_count": result.sec_13f_count,
                "congress_count": result.congress_count,
                "rss_news_count": result.rss_news_count,
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
        """Step 2.5: Collect financial data for active watchlist tickers.

        Skips tickers whose data was collected less than 24 hours ago
        to avoid redundant API calls.
        """
        all_entries = self.watchlist.get_active_tickers_with_staleness()
        if not all_entries:
            self._log("No active tickers to collect data for")
            log_event(
                "collection",
                "collection_skip",
                "No active tickers to collect data for",
                status="skipped",
            )
            return {"collected": 0, "tickers": []}

        # 24-hour cache: skip tickers analyzed within the last day
        now = datetime.now()
        stale_cutoff = now - _ANALYSIS_CACHE_TTL
        tickers: list[str] = []
        cached: list[str] = []
        for entry in all_entries:
            la = entry["last_analyzed"]
            if la and isinstance(la, datetime) and la > stale_cutoff:
                cached.append(entry["ticker"])
            else:
                tickers.append(entry["ticker"])

        if cached:
            self._log(
                f"Skipping {len(cached)} recently-analyzed tickers: "
                f"{', '.join(cached)}"
            )

        if not tickers:
            self._log("All tickers were recently analyzed — nothing to collect")
            return {"collected": 0, "cached": len(cached), "tickers": []}

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
                    log_event(
                        "collection",
                        "collection_ticker_start",
                        f"Collecting data for ${ticker}",
                        ticker=ticker,
                    )
                    pipeline = PipelineService()
                    await pipeline.run(ticker, mode="data")
                    log_event(
                        "collection",
                        "collection_ticker_done",
                        f"${ticker}: data collection complete",
                        ticker=ticker,
                    )
                    self._log(f"➤ Finished data collection for ${ticker}")
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
                    self._log(f"⚠ Failed data collection for ${ticker}: {exc}")
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

    async def _do_embedding(self) -> dict:
        """Step 2.7: Embed newly collected data for RAG retrieval.

        Non-critical: if embedding fails, log warning and return zeroes.
        """
        from app.config import settings

        if not getattr(settings, "RAG_ENABLED", True):
            self._log("RAG disabled — skipping embedding")
            return {"skipped": True, "reason": "RAG disabled"}

        try:
            from app.services.embedding_service import EmbeddingService

            svc = EmbeddingService()

            # Ensure the embedding model is available (auto-pull if needed)
            model_ok = await svc.ensure_model_loaded()
            if not model_ok:
                self._log("⚠️ Embedding model not available — skipping")
                return {"error": "model_not_available", "total_chunks": 0}

            # Embed all sources: YouTube + Reddit + News + Decisions
            result = await svc.embed_all_sources()

            total_chunks = result.get("total_chunks", 0)
            total_embedded = result.get("total_embedded", 0)

            self._log(
                f"📎 Embedded {total_embedded} sources → "
                f"{total_chunks} chunks"
            )

            # Pre-compute query vectors for all active tickers
            # while the embedding model is still loaded in VRAM
            active_tickers = self.watchlist.get_active_tickers()
            if active_tickers:
                self._query_vector_cache = await svc.precompute_query_vectors(
                    active_tickers,
                )
                result["cached_query_vectors"] = len(self._query_vector_cache)
                self._log(
                    f"🔍 Pre-computed {len(self._query_vector_cache)} "
                    f"query vectors for trading"
                )
            else:
                self._query_vector_cache = {}

            return result
        except Exception as exc:
            logger.warning("[AutoLoop] Embedding phase failed: %s", exc)
            self._log(f"⚠️ Embedding failed: {exc}")
            return {"error": str(exc), "total_chunks": 0}

    async def _do_deep_analysis(self) -> dict:
        """Step 3: Run 4-layer analysis on every active watchlist ticker.

        Skips tickers analyzed within the last 24 hours to avoid
        redundant LLM calls.
        """
        all_entries = self.watchlist.get_active_tickers_with_staleness()
        if not all_entries:
            self._log("No active tickers to analyze")
            log_event(
                "analysis",
                "analysis_skip",
                "No active tickers to analyze",
                status="skipped",
            )
            return {"analyzed": 0, "tickers": []}

        now = datetime.now()
        stale_cutoff = now - _ANALYSIS_CACHE_TTL
        tickers: list[str] = []
        cached: list[str] = []
        for entry in all_entries:
            la = entry["last_analyzed"]
            if la and isinstance(la, datetime) and la > stale_cutoff:
                cached.append(entry["ticker"])
            else:
                tickers.append(entry["ticker"])

        if cached:
            self._log(
                f"Skipping {len(cached)} recently-analyzed tickers: "
                f"{', '.join(cached)}"
            )

        if not tickers:
            self._log("All tickers were recently analyzed — nothing to analyze")
            return {"analyzed": 0, "cached": len(cached), "tickers": []}

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
            tickers,
            concurrency=2,
            portfolio_context=portfolio_context,
            bot_id=self.bot_id,
            progress_callback=lambda t: self._log(f"➤ Finished deep analysis for ${t}"),
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
        """Step 4: Make trading decisions — new pipeline or legacy strategist.

        When USE_NEW_PIPELINE is True:
          One LLM call per ticker → TradeAction → ExecutionService
        When False (legacy):
          Multi-turn PortfolioStrategist loop with tool-calling
        """
        from app.config import settings

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

        # ── Cash pre-check ─────────────────────────────────────
        # If cash is below minimum trade value AND no existing positions,
        # skip the trading phase entirely instead of burning LLM turns
        # trying to buy stocks the bot can't afford.
        _MIN_TRADE_CASH = 50.0
        cash = self.paper_trader.get_cash_balance()
        positions = self.paper_trader.get_positions()
        if cash < _MIN_TRADE_CASH and not positions:
            self._log(
                f"Skipping trading: cash=${cash:.2f} below "
                f"minimum ${_MIN_TRADE_CASH:.0f} and no positions"
            )
            log_event(
                "trading",
                "trading_skip",
                f"Insufficient cash (${cash:.2f}) and no positions",
                status="skipped",
                metadata={"cash": cash},
            )
            return {"orders": 0, "tickers": [], "skipped": "insufficient_cash"}
        elif cash < _MIN_TRADE_CASH:
            self._log(
                f"Low cash (${cash:.2f}) — SELL/HOLD decisions only"
            )

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

        # ========================================================
        # New Pipeline (Phase 3+4)
        # ========================================================
        if settings.USE_NEW_PIPELINE:
            self._log(
                f"New Pipeline: analyzing {len(tickers)} tickers "
                f"(dry_run={settings.DRY_RUN_TRADES})…"
            )

            from app.services.trading_pipeline_service import TradingPipelineService

            pipeline = TradingPipelineService(
                paper_trader=self.paper_trader,
                dry_run=settings.DRY_RUN_TRADES,
                bot_id=self.bot_id,
                query_vector_cache=getattr(self, "_query_vector_cache", None),
            )

            try:
                result = await pipeline.run_once(tickers)
            except Exception as exc:
                logger.exception("[AutoLoop] TradingPipeline failed")
                self._log(f"Pipeline error: {exc}")
                log_event(
                    "trading",
                    "pipeline_error",
                    f"TradingPipeline failed: {exc}",
                    status="error",
                )
                return {"orders": 0, "error": str(exc)}

            orders_count = result.get("orders", 0)
            decisions_count = result.get("decisions", 0)

            self._log(
                f"Pipeline: {decisions_count} decisions, "
                f"{orders_count} orders placed"
            )

            log_event(
                "trading",
                "pipeline_complete",
                f"TradingPipeline: {decisions_count} decisions, "
                f"{orders_count} orders",
                metadata={
                    "decisions": decisions_count,
                    "orders": orders_count,
                    "duration_seconds": result.get("duration_seconds", 0),
                },
            )

            # Log individual ticker results for activity feed
            for ticker_result in result.get("tickers", []):
                action = ticker_result.get("action", "?")
                confidence = ticker_result.get("confidence", 0)
                exec_status = ticker_result.get("exec_status", "?")
                log_event(
                    "trading",
                    f"decision_{action.lower()}" if action != "?" else "decision_unknown",
                    f"${ticker_result.get('ticker', '?')}: "
                    f"{action} ({confidence:.0%}) → {exec_status}",
                    ticker=ticker_result.get("ticker"),
                    metadata=ticker_result,
                )

            return {
                "orders": orders_count,
                "decisions": decisions_count,
                "tickers": tickers,
                "duration_seconds": result.get("duration_seconds", 0),
            }

        # ========================================================
        # Legacy: PortfolioStrategist (multi-turn LLM loop)
        # ========================================================
        self._log(
            f"Legacy Strategist: analyzing {len(tickers)} tickers "
            f"for trading decisions…"
        )

        from app.services.portfolio_strategist import PortfolioStrategist
        from app.services.strategist_audit import StrategistAudit

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
        # ── Cancellation gate: skip phase if stop was requested ──
        if self._cancelled:
            self._log(f"⛔ Skipping {phase_name} — emergency stop active")
            return {"status": "cancelled", "duration_seconds": 0}

        self._state["phase"] = phase_name
        self._state["phases"][phase_name] = "running"
        self._log(description)
        logger.info("[AutoLoop] Phase: %s — %s", phase_name, description)

        # Emit phase start to Activity Log
        log_event(
            phase_name, "phase_start", description,
            status="running",
        )

        # Track phase in health tracker
        if hasattr(self, "_health"):
            self._health.start_phase(phase_name)

        t0 = time.time()
        try:
            result = await coro_fn()
            elapsed = round(time.time() - t0, 1)
            result["duration_seconds"] = elapsed
            result["status"] = "success"
            self._state["phases"][phase_name] = "done"
            logger.info("[AutoLoop] Phase %s completed in %.1fs", phase_name, elapsed)

            # Emit phase complete to Activity Log
            log_event(
                phase_name, "phase_complete",
                f"{phase_name.title()} completed in {elapsed}s",
                status="success",
                metadata={"duration_seconds": elapsed},
            )

            if hasattr(self, "_health"):
                self._health.end_phase(phase_name, status="success")

            return result
        except Exception as exc:
            elapsed = round(time.time() - t0, 1)
            self._state["phases"][phase_name] = "error"
            error_msg = f"{phase_name} failed: {exc}"
            self._log(error_msg)
            logger.error("[AutoLoop] %s", error_msg, exc_info=True)

            # Emit phase error to Activity Log
            log_event(
                phase_name, "phase_error",
                f"{phase_name.title()} failed: {exc}",
                status="error",
                metadata={
                    "error": str(exc)[:200],
                    "duration_seconds": elapsed,
                },
            )

            if hasattr(self, "_health"):
                self._health.end_phase(
                    phase_name, status="error", detail=str(exc)[:100],
                )

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
        self._cancelled = False
        self._state = {
            "running": True,
            "phase": "starting",
            "phases": {},
            "started_at": datetime.now().isoformat(),
            "log": [],
        }
