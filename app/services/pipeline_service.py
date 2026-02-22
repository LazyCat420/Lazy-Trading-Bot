"""Pipeline service — orchestrates the full data collection and analysis pipeline."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from app.agents.fundamental_agent import FundamentalAgent
from app.agents.risk_agent import RiskAgent
from app.agents.sentiment_agent import SentimentAgent
from app.agents.technical_agent import TechnicalAgent
from app.collectors.congress_collector import CongressCollector
from app.collectors.news_collector import NewsCollector
from app.collectors.risk_computer import RiskComputer
from app.collectors.rss_news_collector import RSSNewsCollector
from app.collectors.sec_13f_collector import SEC13FCollector
from app.collectors.technical_computer import TechnicalComputer
from app.collectors.yfinance_collector import YFinanceCollector
from app.collectors.youtube_collector import YouTubeCollector
from app.config import settings
from app.engine.aggregator import Aggregator, PooledAnalysis
from app.engine.data_distiller import DataDistiller
from app.engine.quant_signals import QuantSignalEngine
from app.engine.rules_engine import RulesEngine
from app.services.llm_service import LLMService
from app.services.peer_fetcher import PeerFetcher
from app.models.agent_reports import (
    FundamentalReport,
    RiskReport,
    SentimentReport,
    TechnicalReport,
)
from app.models.decision import FinalDecision
from app.utils.logger import logger


class PipelineResult:
    """Full result of a pipeline run."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self.status: dict[str, dict[str, Any]] = {}
        self.pooled: PooledAnalysis | None = None
        self.decision: FinalDecision | None = None
        self.errors: list[str] = []

    def to_dict(self) -> dict:
        """Serialize the pipeline result for API responses."""
        result: dict[str, Any] = {
            "ticker": self.ticker,
            "pipeline_status": self.status,
            "errors": self.errors,
        }



        if self.pooled:
            result["analysis_summary"] = self.pooled.to_summary()
            result["agent_reports"] = self.pooled.full_reports()
        if self.decision:
            result["decision"] = json.loads(self.decision.model_dump_json())
        return result


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
        # Collectors
        self.yf_collector = YFinanceCollector()
        self.tech_computer = TechnicalComputer()
        self.risk_computer = RiskComputer()
        self.news_collector = NewsCollector()
        self.yt_collector = YouTubeCollector()
        self.sec_13f = SEC13FCollector()
        self.congress = CongressCollector()
        self.rss_news = RSSNewsCollector()

        # Agents
        self.technical_agent = TechnicalAgent()
        self.fundamental_agent = FundamentalAgent()
        self.sentiment_agent = SentimentAgent()
        self.risk_agent = RiskAgent()

        # Services
        self.llm_service = LLMService()
        self.peer_fetcher = PeerFetcher(self.llm_service)

        # Engine
        self.aggregator = Aggregator()
        self.rules_engine = RulesEngine()

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
        analyst_data = None
        insider_activity = None
        earnings_calendar = None
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
        async def _step(name: str, coro):  # noqa: ANN001
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
            async def _step_cached(name: str, coro):  # noqa: ANN001
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
            async def _step_data(name: str, coro):  # noqa: ANN001
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
        # Parallel batch: Steps 10, 10b, 11, 12 run concurrently
        # (All depend only on price_history which is already available)
        # ----------------------------------------------------------
        quant_scorecard = None

        if mode != "news" and price_history:
            # Steps 10 + 10b run in parallel (both pure-compute, no I/O conflict)
            async def _step_risk():
                return await self.risk_computer.compute(ticker)

            async def _step_quant():
                return QuantSignalEngine().compute(ticker)

            logger.info("Running Steps 10+10b in parallel for %s …", ticker)
            risk_result, quant_result = await asyncio.gather(
                _step("risk_metrics", _step_risk()),
                _step("quant_scorecard", _step_quant()),
            )
            # Unpack risk metrics
            r_name, r_data, r_exc = risk_result
            if r_exc:
                result.status["risk_metrics"] = {"status": "error", "error": str(r_exc)}
                result.errors.append(f"Risk metrics: {r_exc}")
                logger.error("Step 10 (Risk Metrics) failed: %s", r_exc)
            else:
                risk_metrics = r_data
                result.status["risk_metrics"] = {"status": "ok"}
            # Unpack quant scorecard
            q_name, q_data, q_exc = quant_result
            if q_exc:
                result.status["quant_scorecard"] = {"status": "error", "error": str(q_exc)}
                result.errors.append(f"Quant scorecard: {q_exc}")
                logger.error("Step 10b (Quant Scorecard) failed: %s", q_exc)
            else:
                quant_scorecard = q_data
                result.status["quant_scorecard"] = {
                    "status": "ok",
                    "flags": quant_scorecard.flags if quant_scorecard else [],
                }
                if quant_scorecard:
                    logger.info(
                        "📊 Quant scorecard for %s: %d flags",
                        ticker, len(quant_scorecard.flags),
                    )

        # Steps 11 + 12: News and YouTube scraping run in parallel
        if mode in ("full", "news"):
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
        # PHASE 2: Agent Analysis (Parallel)
        # ============================================================
        logger.info("Starting agent analysis for %s", ticker)

        # Data Distillation — pre-compute structured summaries for LLM
        distiller = DataDistiller()
        distilled_price = distiller.distill_price_action(
            price_history, technicals, quant_scorecard
        )
        distilled_fundamentals = distiller.distill_fundamentals(
            fundamentals, fin_history, balance_sheet, cashflow, quant_scorecard
        )
        distilled_risk = distiller.distill_risk(risk_metrics, quant_scorecard)

        logger.info(
            "📋 Distilled summaries: price=%d chars, fundamentals=%d chars, risk=%d chars",
            len(distilled_price), len(distilled_fundamentals), len(distilled_risk),
        )

        # Load risk params for risk agent
        risk_params_path = settings.USER_CONFIG_DIR / "risk_params.json"
        risk_params = {}
        if risk_params_path.exists():
            risk_params = json.loads(risk_params_path.read_text(encoding="utf-8"))

        # Prepare agent contexts with DISTILLED + RAW data
        ta_context = {
            "price_history": price_history,
            "technicals": technicals,
            "quant_scorecard": quant_scorecard,
            "distilled_analysis": distilled_price,
        }
        fa_context = {
            "fundamentals": fundamentals,
            "financial_history": fin_history,
            "balance_sheet": balance_sheet,
            "cashflow": cashflow,
            "analyst_data": analyst_data,
            "insider_activity": insider_activity,
            "earnings_calendar": earnings_calendar,
            "quant_scorecard": quant_scorecard,
            "distilled_analysis": distilled_fundamentals,
            "industry_peers": industry_peers,
            "peer_fundamentals": peer_fundamentals,
            "institutional_holders": institutional_holders,
        }
        sa_context = {
            "news": news,
            "transcripts": yt_transcripts,
            "institutional_holders": institutional_holders,
            "congress_trades": congress_trades,
            "news_articles": news_articles,
        }
        ra_context = {
            "price_history": price_history,
            "technicals": technicals,
            "fundamentals": fundamentals,
            "risk_metrics": risk_metrics,
            "risk_params": risk_params,
            "quant_scorecard": quant_scorecard,
            "distilled_analysis": distilled_risk,
        }

        # Run agents in parallel
        ta_report: TechnicalReport | None = None
        fa_report: FundamentalReport | None = None
        sa_report: SentimentReport | None = None
        ra_report: RiskReport | None = None

        async def run_agent(name: str, agent: Any, ctx: dict) -> Any:
            try:
                t0 = time.perf_counter()
                logger.info("🚀 Agent [%s] START for %s", name, ticker)
                report = await agent.analyze(ticker, ctx)
                elapsed = time.perf_counter() - t0
                result.status[f"agent_{name}"] = {
                    "status": "ok",
                    "elapsed_s": round(elapsed, 2),
                }
                logger.info(
                    "✅ Agent [%s] DONE  for %s in %.2fs",
                    name, ticker, elapsed,
                )
                return report
            except Exception as e:
                result.status[f"agent_{name}"] = {
                    "status": "error",
                    "error": str(e),
                }
                result.errors.append(f"Agent {name}: {e}")
                logger.error("Agent %s failed: %s", name, e)
                return None

        ta_task = run_agent("technical", self.technical_agent, ta_context)
        fa_task = run_agent("fundamental", self.fundamental_agent, fa_context)
        sa_task = run_agent("sentiment", self.sentiment_agent, sa_context)
        ra_task = run_agent("risk", self.risk_agent, ra_context)

        agents_t0 = time.perf_counter()
        ta_report, fa_report, sa_report, ra_report = await asyncio.gather(
            ta_task, fa_task, sa_task, ra_task
        )
        agents_elapsed = time.perf_counter() - agents_t0
        logger.info(
            "⏱️  All 4 agents completed for %s in %.2fs (parallel)",
            ticker, agents_elapsed,
        )

        # ============================================================
        # PHASE 3: Decision Engine
        # ============================================================
        pooled = self.aggregator.pool(
            ticker,
            technical=ta_report,
            fundamental=fa_report,
            sentiment=sa_report,
            risk=ra_report,
        )
        result.pooled = pooled

        try:
            decision = await self.rules_engine.evaluate(ticker, pooled)
            result.decision = decision
            result.status["decision"] = {"status": "ok"}
        except Exception as e:
            result.status["decision"] = {"status": "error", "error": str(e)}
            result.errors.append(f"Decision: {e}")
            logger.error("Decision engine failed: %s", e)

        # ============================================================
        # Save reports to disk
        # ============================================================
        self._save_reports(ticker, result)

        logger.info("=" * 60)
        logger.info(
            "PIPELINE COMPLETE: %s — %s",
            ticker,
            result.decision.signal if result.decision else "NO DECISION",
        )
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

        agent_names = []
        if mode not in ("data", "news"):
            agent_names = ["technical", "fundamental", "sentiment", "risk"]
        elif mode == "news":
            agent_names = ["sentiment"]

        # Emit the initial plan so the frontend can set up the tracker
        await _emit({
            "type": "plan",
            "steps": all_steps,
            "agents": agent_names,
            "has_decision": mode not in ("data",),
        })

        # ── Phase 1: Data Collection ──
        price_history: list = []
        fundamentals = None
        fin_history: list = []
        technicals: list = []
        balance_sheet: list = []
        cashflow: list = []
        analyst_data = None
        insider_activity = None
        earnings_calendar = None
        risk_metrics = None
        news: list = []
        yt_transcripts: list = []
        industry_peers: list[str] = []
        peer_fundamentals: list = []

        async def _tracked_step(name: str, coro) -> tuple:  # noqa: ANN001
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
                risk_metrics = risk_data
                result.status["risk_metrics"] = {"status": "ok"}

        # Step 11: News
        if mode in ("full", "news"):
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
        if mode in ("full", "news"):
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

        # ── Data Distillation (same as non-streaming run) ──
        quant_scorecard = None
        if price_history:
            try:
                quant_scorecard = QuantSignalEngine().compute(ticker)
            except Exception as e:
                logger.error("Quant scorecard (streaming) failed: %s", e)

        distiller = DataDistiller()
        distilled_price = distiller.distill_price_action(
            price_history, technicals, quant_scorecard
        )
        distilled_fundamentals = distiller.distill_fundamentals(
            fundamentals, fin_history, balance_sheet, cashflow, quant_scorecard
        )
        distilled_risk = distiller.distill_risk(risk_metrics, quant_scorecard)

        # ── Phase 2: Agent Analysis ──
        risk_params_path = settings.USER_CONFIG_DIR / "risk_params.json"
        risk_params = {}
        if risk_params_path.exists():
            risk_params = json.loads(risk_params_path.read_text(encoding="utf-8"))

        ta_context = {
            "price_history": price_history,
            "technicals": technicals,
            "quant_scorecard": quant_scorecard,
            "distilled_analysis": distilled_price,
        }
        fa_context = {
            "fundamentals": fundamentals, "financial_history": fin_history,
            "balance_sheet": balance_sheet, "cashflow": cashflow,
            "analyst_data": analyst_data, "insider_activity": insider_activity,
            "earnings_calendar": earnings_calendar,
            "quant_scorecard": quant_scorecard,
            "distilled_analysis": distilled_fundamentals,
            "industry_peers": industry_peers,
            "peer_fundamentals": peer_fundamentals,
            "institutional_holders": institutional_holders,
        }
        sa_context = {
            "news": news,
            "transcripts": yt_transcripts,
            "institutional_holders": institutional_holders,
            "congress_trades": congress_trades,
            "news_articles": news_articles,
        }
        ra_context = {
            "price_history": price_history, "technicals": technicals,
            "fundamentals": fundamentals, "risk_metrics": risk_metrics,
            "risk_params": risk_params,
            "quant_scorecard": quant_scorecard,
            "distilled_analysis": distilled_risk,
        }

        def _dump_report(report: Any) -> dict | None:
            if report is None:
                return None
            return json.loads(report.model_dump_json())

        async def _run_agent_streaming(name: str, agent: Any, ctx: dict) -> Any:
            await _emit({"type": "agent_start", "name": name})
            try:
                t0 = time.perf_counter()
                logger.info("🚀 Agent [%s] START for %s", name, ticker)
                report = await agent.analyze(ticker, ctx)
                elapsed = time.perf_counter() - t0
                result.status[f"agent_{name}"] = {
                    "status": "ok",
                    "elapsed_s": round(elapsed, 2),
                }
                logger.info(
                    "✅ Agent [%s] DONE  for %s in %.2fs",
                    name, ticker, elapsed,
                )
                await _emit({
                    "type": "agent_complete",
                    "name": name,
                    "report": _dump_report(report),
                    "elapsed_s": round(elapsed, 2),
                })
                return report
            except Exception as e:
                result.status[f"agent_{name}"] = {"status": "error", "error": str(e)}
                result.errors.append(f"Agent {name}: {e}")
                logger.error("Agent %s failed: %s", name, e)
                await _emit({
                    "type": "agent_error",
                    "name": name,
                    "error": str(e),
                })
                return None

        if mode == "news":
            sa_report = await _run_agent_streaming("sentiment", self.sentiment_agent, sa_context)
            ta_report = fa_report = ra_report = None
        else:
            # Run agents truly in parallel BUT emit events individually
            ta_task = _run_agent_streaming("technical", self.technical_agent, ta_context)
            fa_task = _run_agent_streaming("fundamental", self.fundamental_agent, fa_context)
            sa_task = _run_agent_streaming("sentiment", self.sentiment_agent, sa_context)
            ra_task = _run_agent_streaming("risk", self.risk_agent, ra_context)
            ta_report, fa_report, sa_report, ra_report = await asyncio.gather(
                ta_task, fa_task, sa_task, ra_task,
            )

        # ── Phase 3: Decision Engine ──
        pooled = self.aggregator.pool(
            ticker,
            technical=ta_report,
            fundamental=fa_report,
            sentiment=sa_report,
            risk=ra_report,
        )
        result.pooled = pooled

        try:
            decision = await self.rules_engine.evaluate(ticker, pooled)
            result.decision = decision
            result.status["decision"] = {"status": "ok"}
            await _emit({
                "type": "decision_complete",
                "decision": json.loads(decision.model_dump_json()),
            })
        except Exception as e:
            result.status["decision"] = {"status": "error", "error": str(e)}
            result.errors.append(f"Decision: {e}")
            logger.error("Decision engine failed: %s", e)
            await _emit({"type": "decision_error", "error": str(e)})

        self._save_reports(ticker, result)

        await _emit({
            "type": "done",
            "pipeline_status": result.status,
            "errors": result.errors,
        })

        logger.info("=" * 60)
        logger.info(
            "PIPELINE STREAM COMPLETE: %s — %s",
            ticker,
            result.decision.signal if result.decision else "NO DECISION",
        )
        logger.info("=" * 60)

        return result

    def _save_reports(self, ticker: str, result: PipelineResult) -> None:
        """Save agent reports and decision to disk for debugging/backtesting."""
        today = datetime.now().strftime("%Y-%m-%d")
        report_dir = settings.REPORTS_DIR / ticker / today
        report_dir.mkdir(parents=True, exist_ok=True)

        if result.pooled:
            if result.pooled.technical:
                (report_dir / "technical_report.json").write_text(
                    result.pooled.technical.model_dump_json(indent=2),
                    encoding="utf-8",
                )
            if result.pooled.fundamental:
                (report_dir / "fundamental_report.json").write_text(
                    result.pooled.fundamental.model_dump_json(indent=2),
                    encoding="utf-8",
                )
            if result.pooled.sentiment:
                (report_dir / "sentiment_report.json").write_text(
                    result.pooled.sentiment.model_dump_json(indent=2),
                    encoding="utf-8",
                )
            if result.pooled.risk:
                (report_dir / "risk_report.json").write_text(
                    result.pooled.risk.model_dump_json(indent=2),
                    encoding="utf-8",
                )

            # Pooled summary
            (report_dir / "pooled_analysis.json").write_text(
                json.dumps(result.pooled.to_summary(), indent=2),
                encoding="utf-8",
            )

        if result.decision:
            (report_dir / "final_decision.json").write_text(
                result.decision.model_dump_json(indent=2),
                encoding="utf-8",
            )

        logger.info("Reports saved to %s", report_dir)
