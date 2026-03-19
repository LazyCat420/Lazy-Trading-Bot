"""Pipeline service — orchestrates the full data collection and analysis pipeline."""

from __future__ import annotations

from app.services.unified_logger import track_class_telemetry, track_telemetry
import asyncio
import json
from datetime import datetime
from typing import Any

from app.config import settings
from app.services.congress_service import CongressCollector
from app.services.llm_service import LLMService
from app.services.news_service import NewsCollector
from app.services.peer_fetcher import PeerFetcher
from app.services.quant_engine import QuantSignalEngine
from app.services.risk_service import RiskComputer
from app.services.rss_news_service import RSSNewsCollector
from app.services.sec_13f_service import SEC13FCollector
from app.services.technical_service import TechnicalComputer
from app.services.yfinance_service import YFinanceCollector
from app.services.youtube_service import YouTubeCollector
from app.utils.logger import logger


@track_class_telemetry
class PipelineResult:
    """Result of a data collection pipeline run."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self.status: dict[str, dict[str, Any]] = {}
        self.errors: list[str] = []

    def to_dict(self) -> dict:
        """Serialize the pipeline result for API responses."""
        return {
            "ticker": self.ticker,
            "pipeline_status": self.status,
            "errors": self.errors,
        }


@track_class_telemetry
class PipelineService:
    """Orchestrates the full trading analysis pipeline for a single ticker.

    Data Collection Steps:
        1. Price History (OHLCV)
        2. Fundamentals (.info snapshot)
        3. Financial History (income statement)
        4. Technical Indicators (pandas-ta → 154 indicators)
        5. Balance Sheet (multi-year)
        6. Cash Flow Statement (multi-year)
        7. Analyst Data (targets + recommendations)
        8. Insider Activity (transactions + institutional %)
        9. Earnings Calendar (next date + surprise)
       10. Risk Metrics (RiskComputer — 25+ quant metrics)
       11. News (yfinance + Google News RSS + SEC EDGAR)
       12. YouTube Transcripts (yt-dlp + transcript API)
    """

    def __init__(self) -> None:
        # Data collection services
        self.yf_collector = YFinanceCollector()
        self.tech_computer = TechnicalComputer()
        self.risk_computer = RiskComputer()
        self.news_collector = NewsCollector()
        self.yt_collector = YouTubeCollector()
        self.sec_13f = SEC13FCollector()
        self.congress = CongressCollector()
        self.rss_news = RSSNewsCollector()

        # Services
        self.llm_service = LLMService()
        self.peer_fetcher = PeerFetcher(self.llm_service)

    async def run(
        self,
        ticker: str,
        mode: str = "full",
    ) -> PipelineResult:
        """Execute the pipeline.

        Modes:
            full  — All 12 data steps + 4 agents + decision
            quick — Steps 1,4 (price + technicals) + agents + decision
            news  — Steps 11,12 (news + YouTube) + sentiment agent only
            data  — Steps 1-12 only (no agents, no decision)
        """
        logger.info("=" * 60)
        logger.info("PIPELINE START: %s (mode=%s)", ticker, mode)
        logger.info("=" * 60)

        result = PipelineResult(ticker)

        # ============================================================
        # PHASE 1: Data Collection
        # ============================================================
        price_history: list = []
        fundamentals = None
        fin_history: list = []
        technicals: list = []
        balance_sheet: list = []
        cashflow: list = []
        analyst_data = None  # collected & stored to DB; used by analyze_ticker()
        insider_activity = None  # collected & stored to DB; used by analyze_ticker()
        earnings_calendar = None  # collected & stored to DB; used by analyze_ticker()
        risk_metrics = None
        news: list = []
        yt_transcripts: list = []
        industry_peers: list[str] = []
        peer_fundamentals: list = []
        institutional_holders: list = []
        congress_trades: list = []
        news_articles: list = []

        # ----------------------------------------------------------
        # Helper for parallel steps
        # ----------------------------------------------------------
        async def _step(name: str, coro):
            try:
                data = await coro
                return name, data, None
            except Exception as exc:
                return name, None, exc

        # ----------------------------------------------------------
        # Parallel batch: Steps 1–9 run concurrently when possible
        # (Steps 4 & 10 depend on price data — they run after)
        # ----------------------------------------------------------
        if mode == "full":
            # All 9 yfinance steps can run in parallel (daily guards
            # ensure only new data hits Yahoo).

            parallel_tasks = [
                _step("price_history", self.yf_collector.collect_price_history(ticker)),
                _step("fundamentals", self.yf_collector.collect_fundamentals(ticker)),
                _step("financial_history", self.yf_collector.collect_financial_history(ticker)),
                _step("balance_sheet", self.yf_collector.collect_balance_sheet(ticker)),
                _step("cashflow", self.yf_collector.collect_cashflow(ticker)),
                _step("analyst_data", self.yf_collector.collect_analyst_data(ticker)),
                _step("insider_activity", self.yf_collector.collect_insider_activity(ticker)),
                _step("earnings_calendar", self.yf_collector.collect_earnings_calendar(ticker)),
            ]

            logger.info("Running Steps 1-9 in parallel for %s …", ticker)
            results_batch = await asyncio.gather(*parallel_tasks)

            # Unpack results
            for name, data, exc in results_batch:
                if exc:
                    result.status[name] = {"status": "error", "error": str(exc)}
                    result.errors.append(f"{name}: {exc}")
                    logger.error("Step (%s) failed: %s", name, exc)
                else:
                    if name == "price_history":
                        price_history = data or []
                        result.status[name] = {"status": "ok", "rows": len(price_history)}
                    elif name == "fundamentals":
                        fundamentals = data
                        result.status[name] = {"status": "ok"}
                    elif name == "financial_history":
                        fin_history = data or []
                        result.status[name] = {"status": "ok", "years": len(fin_history)}
                    elif name == "balance_sheet":
                        balance_sheet = data or []
                        result.status[name] = {"status": "ok", "years": len(balance_sheet)}
                    elif name == "cashflow":
                        cashflow = data or []
                        result.status[name] = {"status": "ok", "years": len(cashflow)}
                    elif name == "analyst_data":
                        analyst_data = data
                        result.status[name] = {"status": "ok"}
                    elif name == "insider_activity":
                        insider_activity = data
                        result.status[name] = {"status": "ok"}
                    elif name == "earnings_calendar":
                        earnings_calendar = data
                        result.status[name] = {"status": "ok"}

        elif mode == "quick":
            # Quick mode: fresh price history + cached fundamentals/news from DB
            # The yfinance daily guards return stored data without hitting Yahoo
            try:
                price_history = await self.yf_collector.collect_price_history(ticker)
                result.status["price_history"] = {"status": "ok", "rows": len(price_history)}
            except Exception as e:
                result.status["price_history"] = {"status": "error", "error": str(e)}
                result.errors.append(f"Price history: {e}")
                logger.error("Step 1 (Price) failed: %s", e)

            # Load cached fundamentals/financials from DB (daily guards = no Yahoo calls)
            async def _step_cached(name: str, coro):
                try:
                    data = await coro
                    return name, data, None
                except Exception as exc:
                    return name, None, exc

            cached_tasks = [
                _step_cached("fundamentals", self.yf_collector.collect_fundamentals(ticker)),
                _step_cached("financial_history", self.yf_collector.collect_financial_history(ticker)),
                _step_cached("balance_sheet", self.yf_collector.collect_balance_sheet(ticker)),
                _step_cached("cashflow", self.yf_collector.collect_cashflow(ticker)),
                _step_cached("analyst_data", self.yf_collector.collect_analyst_data(ticker)),
                _step_cached("insider_activity", self.yf_collector.collect_insider_activity(ticker)),
            ]

            logger.info("Loading cached fundamentals for %s (quick mode) …", ticker)
            cached_results = await asyncio.gather(*cached_tasks)

            for name, data, exc in cached_results:
                if exc:
                    # Non-fatal — quick mode just logs and continues
                    logger.debug("Quick mode cached %s for %s: %s (non-fatal)", name, ticker, exc)
                else:
                    if name == "fundamentals":
                        fundamentals = data
                    elif name == "financial_history":
                        fin_history = data or []
                    elif name == "balance_sheet":
                        balance_sheet = data or []
                    elif name == "cashflow":
                        cashflow = data or []
                    elif name == "analyst_data":
                        analyst_data = data
                    elif name == "insider_activity":
                        insider_activity = data
                    result.status[f"cached_{name}"] = {"status": "ok"}

            # Load historical news and transcripts from DB (no scraping)
            try:
                news = await self.news_collector.get_all_historical(ticker)
                result.status["cached_news"] = {"status": "ok", "articles": len(news)}
                logger.info("Loaded %d cached news articles for %s", len(news), ticker)
            except Exception as e:
                logger.debug("No cached news for %s: %s", ticker, e)

            try:
                yt_transcripts = await self.yt_collector.get_all_historical(ticker)
                result.status["cached_youtube"] = {"status": "ok", "transcripts": len(yt_transcripts)}
                logger.info("Loaded %d cached transcripts for %s", len(yt_transcripts), ticker)
            except Exception as e:
                logger.debug("No cached transcripts for %s: %s", ticker, e)

        elif mode == "data":
            # Data-only: same parallel batch as full
            async def _step_data(name: str, coro):
                try:
                    data = await coro
                    return name, data, None
                except Exception as exc:
                    return name, None, exc

            parallel_tasks_data = [
                _step_data("price_history", self.yf_collector.collect_price_history(ticker)),
                _step_data("fundamentals", self.yf_collector.collect_fundamentals(ticker)),
                _step_data("financial_history", self.yf_collector.collect_financial_history(ticker)),
                _step_data("balance_sheet", self.yf_collector.collect_balance_sheet(ticker)),
                _step_data("cashflow", self.yf_collector.collect_cashflow(ticker)),
                _step_data("analyst_data", self.yf_collector.collect_analyst_data(ticker)),
                _step_data("insider_activity", self.yf_collector.collect_insider_activity(ticker)),
                _step_data("earnings_calendar", self.yf_collector.collect_earnings_calendar(ticker)),
            ]

            logger.info("Running Steps 1-9 in parallel (data mode) for %s …", ticker)
            results_data = await asyncio.gather(*parallel_tasks_data)

            for name, data, exc in results_data:
                if exc:
                    result.status[name] = {"status": "error", "error": str(exc)}
                    result.errors.append(f"{name}: {exc}")
                    logger.error("Step (%s) failed: %s", name, exc)
                else:
                    if name == "price_history":
                        price_history = data or []
                        result.status[name] = {"status": "ok", "rows": len(price_history)}
                    elif name == "fundamentals":
                        fundamentals = data
                        result.status[name] = {"status": "ok"}
                    elif name == "financial_history":
                        fin_history = data or []
                        result.status[name] = {"status": "ok", "years": len(fin_history)}
                    elif name == "balance_sheet":
                        balance_sheet = data or []
                        result.status[name] = {"status": "ok", "years": len(balance_sheet)}
                    elif name == "cashflow":
                        cashflow = data or []
                        result.status[name] = {"status": "ok", "years": len(cashflow)}
                    elif name == "analyst_data":
                        analyst_data = data
                        result.status[name] = {"status": "ok"}
                    elif name == "insider_activity":
                        insider_activity = data
                        result.status[name] = {"status": "ok"}
                    elif name == "earnings_calendar":
                        earnings_calendar = data
                        result.status[name] = {"status": "ok"}

        # --- news mode skips yfinance entirely ---

        # Step 4: Technical Indicators (depends on price data)
        if mode != "news" and price_history:
            try:
                technicals = await self.tech_computer.compute(ticker)
                result.status["technicals"] = {
                    "status": "ok",
                    "rows": len(technicals),
                }
            except Exception as e:
                result.status["technicals"] = {"status": "error", "error": str(e)}
                result.errors.append(f"Technicals: {e}")
                logger.error("Step 4 (Technicals) failed: %s", e)

        # ----------------------------------------------------------
        # Step 10: Risk Metrics (depends on price data)
        # Step 10b: Quant Scorecard (skipped in data mode — recomputed
        #   by DeepAnalysis Layer 1 during the analysis phase)
        # ----------------------------------------------------------
        quant_scorecard = None

        if mode != "news" and price_history:
            # Step 10: Risk metrics — always needed (stored in DuckDB)
            async def _step_risk():
                return await self.risk_computer.compute(ticker)

            try:
                _risk_metrics = await _step_risk()
                result.status["risk_metrics"] = {"status": "ok"}
            except Exception as e:
                result.status["risk_metrics"] = {
                    "status": "error", "error": str(e),
                }
                result.errors.append(f"Risk metrics: {e}")
                logger.error("Step 10 (Risk Metrics) failed: %s", e)

            # Step 10b: Quant scorecard — only for full/quick modes
            # (data mode skips this; DeepAnalysis recomputes it)
            if mode != "data":
                async def _step_quant():
                    return QuantSignalEngine().compute(ticker)

                try:
                    quant_scorecard = await _step_quant()
                    result.status["quant_scorecard"] = {
                        "status": "ok",
                        "flags": (
                            quant_scorecard.flags
                            if quant_scorecard else []
                        ),
                    }
                    if quant_scorecard:
                        logger.info(
                            "📊 Quant scorecard for %s: %d flags",
                            ticker, len(quant_scorecard.flags),
                        )
                except Exception as e:
                    result.status["quant_scorecard"] = {
                        "status": "error",
                        "error": str(e),
                    }
                    result.errors.append(
                        f"Quant scorecard: {e}"
                    )
                    logger.error(
                        "Step 10b (Quant Scorecard) failed: %s",
                        e,
                    )

        # Steps 11 + 12: News and YouTube scraping run in parallel
        if mode in ("full", "news", "data"):
            async def _step_news():
                await self.news_collector.collect(ticker)
                return await self.news_collector.get_all_historical(ticker)

            async def _step_youtube():
                await self.yt_collector.collect(ticker)
                return await self.yt_collector.get_all_historical(ticker)

            logger.info("Running Steps 11+12 (News+YouTube) in parallel for %s …", ticker)
            news_result, yt_result = await asyncio.gather(
                _step("news", _step_news()),
                _step("youtube", _step_youtube()),
            )
            # Unpack news
            n_name, n_data, n_exc = news_result
            if n_exc:
                result.status["news"] = {"status": "error", "error": str(n_exc)}
                result.errors.append(f"News: {n_exc}")
                logger.error("Step 11 (News) failed: %s", n_exc)
            else:
                news = n_data or []
                result.status["news"] = {"status": "ok", "articles": len(news)}
            # Unpack YouTube
            y_name, y_data, y_exc = yt_result
            if y_exc:
                result.status["youtube"] = {"status": "error", "error": str(y_exc)}
                result.errors.append(f"YouTube: {y_exc}")
                logger.error("Step 12 (YouTube) failed: %s", y_exc)
            else:
                yt_transcripts = y_data or []
                result.status["youtube"] = {"status": "ok", "total_transcripts": len(yt_transcripts)}

        # Step 13: Fetch Industry Peers and their Fundamentals
        if mode in ("full", "data"):
            try:
                industry_peers = await self.peer_fetcher.get_industry_peers(ticker, fundamentals)
                if industry_peers:
                    # Fetch fundamentals for peers
                    peer_tasks = [self.yf_collector.collect_fundamentals(peer) for peer in industry_peers]
                    peer_results = await asyncio.gather(*peer_tasks, return_exceptions=True)
                    for peer, peer_data in zip(industry_peers, peer_results):
                        if not isinstance(peer_data, Exception) and peer_data:
                            peer_fundamentals.append(peer_data)
                    result.status["industry_peers"] = {"status": "ok", "peers": industry_peers}
            except Exception as e:
                result.status["industry_peers"] = {"status": "error", "error": str(e)}
                logger.error("Step 13 (Industry Peers) failed: %s", e)

        # If data-only mode, stop here
        if mode == "data":
            logger.info("Data-only mode complete for %s", ticker)
            return result

        # Step 14: Smart Money data (13F + Congress + RSS News) — parallel
        if mode in ("full", "quick"):
            logger.info("Running Steps 14a+14b+14c (Smart Money) in parallel for %s …", ticker)
            sm_results = await asyncio.gather(
                _step("institutional_holders", self.sec_13f.get_holdings_for_ticker(ticker)),
                _step("congress_trades", self.congress.get_trades_for_ticker(ticker)),
                _step("news_articles", self.rss_news.get_articles_for_ticker(ticker)),
            )
            for sm_name, sm_data, sm_exc in sm_results:
                if sm_exc:
                    result.status[sm_name] = {"status": "error", "error": str(sm_exc)}
                    result.errors.append(f"{sm_name}: {sm_exc}")
                    logger.error("Step 14 (%s) failed: %s", sm_name, sm_exc)
                else:
                    if sm_name == "institutional_holders":
                        institutional_holders = sm_data or []
                        result.status[sm_name] = {"status": "ok", "holders": len(institutional_holders)}
                        logger.info("Step 14a: %d institutional holders for %s", len(institutional_holders), ticker)
                    elif sm_name == "congress_trades":
                        congress_trades = sm_data or []
                        result.status[sm_name] = {"status": "ok", "trades": len(congress_trades)}
                        logger.info("Step 14b: %d congressional trades for %s", len(congress_trades), ticker)
                    elif sm_name == "news_articles":
                        news_articles = sm_data or []
                        result.status[sm_name] = {"status": "ok", "articles": len(news_articles)}
                        logger.info("Step 14c: %d news articles for %s", len(news_articles), ticker)

        # Clear the ticker cache after data collection
        YFinanceCollector.clear_cache(ticker)

        # ============================================================
        # PHASE 2: Data collection complete — save reports
        # ============================================================
        self._save_reports(ticker, result)

        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETE: %s — data collection done", ticker)
        logger.info("=" * 60)

        return result

    async def run_streaming(
        self,
        ticker: str,
        mode: str,
        queue: asyncio.Queue,
    ) -> PipelineResult:
        """Execute the pipeline, pushing progress events to *queue*.

        Events are dicts with a ``type`` key:
            step_start        – a data-collection step began
            step_complete     – a data-collection step finished
            step_error        – a data-collection step failed
            agent_complete    – an LLM agent returned its report
            decision_complete – the decision engine produced a verdict
            done              – pipeline finished (includes final status)
        """

        async def _emit(event: dict) -> None:
            await queue.put(event)

        logger.info("=" * 60)
        logger.info("PIPELINE STREAM START: %s (mode=%s)", ticker, mode)
        logger.info("=" * 60)

        result = PipelineResult(ticker)

        # ── Define the step catalogue for the progress tracker ──
        all_steps: list[str] = []
        if mode == "full":
            all_steps = [
                "price_history", "fundamentals", "financial_history",
                "balance_sheet", "cashflow", "analyst_data",
                "insider_activity", "earnings_calendar",
                "technicals", "risk_metrics",
                "news_scrape", "news", "youtube_scrape", "youtube",
            ]
        elif mode == "quick":
            all_steps = ["price_history", "technicals"]
        elif mode == "data":
            all_steps = [
                "price_history", "fundamentals", "financial_history",
                "balance_sheet", "cashflow", "analyst_data",
                "insider_activity", "earnings_calendar",
                "technicals", "risk_metrics",
                "news_scrape", "news", "youtube_scrape", "youtube",
            ]
        elif mode == "news":
            all_steps = ["news_scrape", "news", "youtube_scrape", "youtube"]

        # Emit the initial plan so the frontend can set up the tracker
        await _emit({
            "type": "plan",
            "steps": all_steps,
            "agents": [],
            "has_decision": False,
        })

        # ── Phase 1: Data Collection ──
        price_history: list = []
        fundamentals = None
        fin_history: list = []
        technicals: list = []
        balance_sheet: list = []
        cashflow: list = []
        analyst_data = None  # collected & stored to DB; used by analyze_ticker()
        insider_activity = None  # collected & stored to DB; used by analyze_ticker()
        earnings_calendar = None  # collected & stored to DB; used by analyze_ticker()
        risk_metrics = None
        news: list = []
        yt_transcripts: list = []
        industry_peers: list[str] = []
        peer_fundamentals: list = []

        async def _tracked_step(name: str, coro) -> tuple:
            """Run a step, emitting start/complete/error events."""
            await _emit({"type": "step_start", "name": name})
            try:
                data = await coro
                detail = {}
                if isinstance(data, list):
                    detail["rows"] = len(data)
                await _emit({
                    "type": "step_complete",
                    "name": name,
                    "status": "ok",
                    **detail,
                })
                return name, data, None
            except Exception as exc:
                await _emit({
                    "type": "step_error",
                    "name": name,
                    "error": str(exc),
                })
                return name, None, exc

        # ── Parallel batch: Steps 1-9 ──
        if mode in ("full", "data"):
            parallel_tasks = [
                _tracked_step("price_history", self.yf_collector.collect_price_history(ticker)),
                _tracked_step("fundamentals", self.yf_collector.collect_fundamentals(ticker)),
                _tracked_step("financial_history", self.yf_collector.collect_financial_history(ticker)),
                _tracked_step("balance_sheet", self.yf_collector.collect_balance_sheet(ticker)),
                _tracked_step("cashflow", self.yf_collector.collect_cashflow(ticker)),
                _tracked_step("analyst_data", self.yf_collector.collect_analyst_data(ticker)),
                _tracked_step("insider_activity", self.yf_collector.collect_insider_activity(ticker)),
                _tracked_step("earnings_calendar", self.yf_collector.collect_earnings_calendar(ticker)),
            ]
            results_batch = await asyncio.gather(*parallel_tasks)

            for name, data, exc in results_batch:
                if exc:
                    result.status[name] = {"status": "error", "error": str(exc)}
                    result.errors.append(f"{name}: {exc}")
                else:
                    if name == "price_history":
                        price_history = data or []
                        result.status[name] = {"status": "ok", "rows": len(price_history)}
                    elif name == "fundamentals":
                        fundamentals = data
                        result.status[name] = {"status": "ok"}
                    elif name == "financial_history":
                        fin_history = data or []
                        result.status[name] = {"status": "ok", "years": len(fin_history)}
                    elif name == "balance_sheet":
                        balance_sheet = data or []
                        result.status[name] = {"status": "ok", "years": len(balance_sheet)}
                    elif name == "cashflow":
                        cashflow = data or []
                        result.status[name] = {"status": "ok", "years": len(cashflow)}
                    elif name == "analyst_data":
                        analyst_data = data
                        result.status[name] = {"status": "ok"}
                    elif name == "insider_activity":
                        insider_activity = data
                        result.status[name] = {"status": "ok"}
                    elif name == "earnings_calendar":
                        earnings_calendar = data
                        result.status[name] = {"status": "ok"}

        elif mode == "quick":
            _, price_data, price_exc = await _tracked_step(
                "price_history", self.yf_collector.collect_price_history(ticker),
            )
            if price_exc:
                result.status["price_history"] = {"status": "error", "error": str(price_exc)}
                result.errors.append(f"Price history: {price_exc}")
            else:
                price_history = price_data or []
                result.status["price_history"] = {"status": "ok", "rows": len(price_history)}

        # Step 4: Technicals
        if mode != "news" and price_history:
            _, tech_data, tech_exc = await _tracked_step(
                "technicals", self.tech_computer.compute(ticker),
            )
            if tech_exc:
                result.status["technicals"] = {"status": "error", "error": str(tech_exc)}
                result.errors.append(f"Technicals: {tech_exc}")
            else:
                technicals = tech_data or []
                result.status["technicals"] = {"status": "ok", "rows": len(technicals)}

        # Step 10: Risk Metrics
        if mode != "news" and price_history:
            _, risk_data, risk_exc = await _tracked_step(
                "risk_metrics", self.risk_computer.compute(ticker),
            )
            if risk_exc:
                result.status["risk_metrics"] = {"status": "error", "error": str(risk_exc)}
                result.errors.append(f"Risk metrics: {risk_exc}")
            else:
                risk_metrics = risk_data  # noqa: F841
                result.status["risk_metrics"] = {"status": "ok"}

        # Step 11: News
        if mode in ("full", "news", "data"):
            _, _, ns_exc = await _tracked_step(
                "news_scrape", self.news_collector.collect(ticker),
            )
            if ns_exc:
                result.status["news_scrape"] = {"status": "error", "error": str(ns_exc)}
                result.errors.append(f"News scrape: {ns_exc}")

            _, news_data, nr_exc = await _tracked_step(
                "news", self.news_collector.get_all_historical(ticker),
            )
            if nr_exc:
                result.status["news"] = {"status": "error", "error": str(nr_exc)}
                result.errors.append(f"News retrieval: {nr_exc}")
            else:
                news = news_data or []
                result.status["news"] = {"status": "ok", "articles": len(news)}

        # Step 12: YouTube
        if mode in ("full", "news", "data"):
            _, yt_new, ys_exc = await _tracked_step(
                "youtube_scrape", self.yt_collector.collect(ticker),
            )
            if ys_exc:
                result.status["youtube_scrape"] = {"status": "error", "error": str(ys_exc)}
                result.errors.append(f"YouTube scrape: {ys_exc}")

            _, yt_data, yr_exc = await _tracked_step(
                "youtube", self.yt_collector.get_all_historical(ticker),
            )
            if yr_exc:
                result.status["youtube"] = {"status": "error", "error": str(yr_exc)}
                result.errors.append(f"YouTube retrieval: {yr_exc}")
            else:
                yt_transcripts = yt_data or []
                result.status["youtube"] = {"status": "ok", "total_transcripts": len(yt_transcripts)}

        # Step 13: Fetch Industry Peers and their Fundamentals
        if mode in ("full", "data"):
            await _emit({"type": "step_start", "name": "industry_peers"})
            try:
                industry_peers = await self.peer_fetcher.get_industry_peers(ticker, fundamentals)
                if industry_peers:
                    # Fetch fundamentals for peers
                    peer_tasks = [self.yf_collector.collect_fundamentals(peer) for peer in industry_peers]
                    peer_results = await asyncio.gather(*peer_tasks, return_exceptions=True)
                    for peer, peer_data in zip(industry_peers, peer_results):
                        if not isinstance(peer_data, Exception) and peer_data:
                            peer_fundamentals.append(peer_data)
                    result.status["industry_peers"] = {"status": "ok", "peers": industry_peers}
                    await _emit({"type": "step_complete", "name": "industry_peers", "status": "ok", "peers": industry_peers})
                else:
                    await _emit({"type": "step_complete", "name": "industry_peers", "status": "warning", "peers": []})
            except Exception as exc:
                result.status["industry_peers"] = {"status": "error", "error": str(exc)}
                await _emit({"type": "step_error", "name": "industry_peers", "error": str(exc)})

        # If data-only, stop here
        if mode == "data":
            await _emit({"type": "done", "pipeline_status": result.status, "errors": result.errors})
            return result

        # Step 14: Smart Money data (parallel) — same as non-streaming run()
        institutional_holders: list = []
        congress_trades: list = []
        news_articles: list = []
        if mode in ("full", "quick"):
            logger.info("Running Steps 14a+14b+14c (Smart Money) in parallel for %s …", ticker)
            sm_results = await asyncio.gather(
                _tracked_step("institutional_holders", self.sec_13f.get_holdings_for_ticker(ticker)),
                _tracked_step("congress_trades", self.congress.get_trades_for_ticker(ticker)),
                _tracked_step("news_articles", self.rss_news.get_articles_for_ticker(ticker)),
            )
            for sm_name, sm_data, sm_exc in sm_results:
                if sm_exc:
                    result.status[sm_name] = {"status": "error", "error": str(sm_exc)}
                    result.errors.append(f"{sm_name}: {sm_exc}")
                else:
                    if sm_name == "institutional_holders":
                        institutional_holders = sm_data or []
                        result.status[sm_name] = {"status": "ok", "holders": len(institutional_holders)}
                    elif sm_name == "congress_trades":
                        congress_trades = sm_data or []
                        result.status[sm_name] = {"status": "ok", "trades": len(congress_trades)}
                    elif sm_name == "news_articles":
                        news_articles = sm_data or []
                        result.status[sm_name] = {"status": "ok", "articles": len(news_articles)}

        YFinanceCollector.clear_cache(ticker)

        # ── Data collection complete ──
        self._save_reports(ticker, result)

        await _emit({
            "type": "done",
            "pipeline_status": result.status,
            "errors": result.errors,
        })

        logger.info("=" * 60)
        logger.info("PIPELINE STREAM COMPLETE: %s — data collection done", ticker)
        logger.info("=" * 60)

        return result

    def _save_reports(self, ticker: str, result: PipelineResult) -> None:
        """Save pipeline status to disk for debugging."""
        today = datetime.now().strftime("%Y-%m-%d")
        report_dir = settings.REPORTS_DIR / ticker / today
        report_dir.mkdir(parents=True, exist_ok=True)

        (report_dir / "pipeline_status.json").write_text(
            json.dumps(result.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )

        logger.info("Pipeline status saved to %s", report_dir)
