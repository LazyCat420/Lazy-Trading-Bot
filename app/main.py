"""FastAPI application — API endpoints + frontend dashboard."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import StreamingResponse

from app.config import settings
from app.database import get_db
from app.services.llm_service import LLMService
from app.services.pipeline_service import PipelineService
from app.utils.logger import logger

app = FastAPI(
    title="Lazy Trading Bot",
    description="Modular trading analysis pipeline with custom user strategy",
    version="0.1.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files + Templates
_app_dir = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(_app_dir / "static")), name="static")
templates = Jinja2Templates(directory=str(_app_dir / "templates"))


# ── Models ──────────────────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    ticker: str = "NVDA"
    mode: str = "full"  # full | quick | news | data


class StrategyUpdateRequest(BaseModel):
    strategy_text: str


class RiskParamsUpdateRequest(BaseModel):
    params: dict


class WatchlistUpdateRequest(BaseModel):
    tickers: list[str]


class WatchlistAddRequest(BaseModel):
    ticker: str
    source: str = "manual"
    notes: str = ""


class WatchlistImportRequest(BaseModel):
    min_score: float = 3.0
    max_tickers: int = 10


class PortfolioResetRequest(BaseModel):
    balance: float | None = None  # If None, reads from risk_params.json


class LLMConfigRequest(BaseModel):
    provider: str | None = None
    ollama_url: str | None = None
    lmstudio_url: str | None = None
    model: str | None = None
    context_size: int | None = None
    temperature: float | None = None


# ── Singleton services ──────────────────────────────────────────────
pipeline = PipelineService()

# Lazy import to avoid circular — WatchlistManager uses PipelineService
from app.services.watchlist_manager import WatchlistManager  # noqa: E402
from app.services.deep_analysis_service import DeepAnalysisService  # noqa: E402

_watchlist_mgr = WatchlistManager()
_deep_analysis = DeepAnalysisService()


# ── Helpers ─────────────────────────────────────────────────────────
def _json_serial(obj: object) -> str:
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    msg = f"Type {type(obj)} not serializable"
    raise TypeError(msg)


def _query_to_dicts(sql: str, params: list | None = None) -> list[dict]:
    """Run a DuckDB query and return rows as list of dicts."""
    db = get_db()
    result = db.execute(sql, params or []).fetchall()
    cols = [desc[0] for desc in db.description]
    return [dict(zip(cols, row)) for row in result]


def _safe_float(val: object) -> float | None:
    """Safely convert a value to float."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════
# FRONTEND ROUTES
# ══════════════════════════════════════════════════════════════════════


@app.get("/", response_class=HTMLResponse)
async def root(request: Request) -> HTMLResponse:
    """Serve the terminal dashboard."""
    return templates.TemplateResponse("index.html", {"request": request})


# ══════════════════════════════════════════════════════════════════════
# EXISTING API ROUTES (unchanged)
# ══════════════════════════════════════════════════════════════════════


@app.get("/api/health")
async def health() -> dict:
    """Detailed health check including LLM status."""
    llm = LLMService()
    llm_status = await llm.health_check()
    return {
        "api": "ok",
        "llm": llm_status,
        "config": {
            "provider": settings.LLM_PROVIDER,
            "model": settings.LLM_MODEL,
            "base_url": settings.LLM_BASE_URL,
        },
    }


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest) -> dict:
    """Run the full analysis pipeline for a ticker."""
    ticker = req.ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")

    logger.info("API: analyze %s (mode=%s)", ticker, req.mode)

    try:
        result = await pipeline.run(ticker, mode=req.mode)
        return result.to_dict()
    except Exception as e:
        logger.error("Pipeline failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/analyze-stream")
async def analyze_stream(
    ticker: str = Query(...),
    mode: str = Query(default="full"),
) -> StreamingResponse:
    """SSE endpoint — streams pipeline progress events in real-time."""
    ticker = ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")

    logger.info("API: analyze-stream %s (mode=%s)", ticker, mode)
    queue: asyncio.Queue = asyncio.Queue()

    async def _run_pipeline() -> None:
        try:
            await pipeline.run_streaming(ticker, mode=mode, queue=queue)
        except Exception as e:
            logger.error("Streaming pipeline failed: %s", e, exc_info=True)
            await queue.put({"type": "error", "error": str(e)})
        finally:
            await queue.put(None)  # sentinel

    async def _event_generator():
        task = asyncio.create_task(_run_pipeline())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event, default=_json_serial)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/watchlist")
async def get_watchlist() -> dict:
    """Get the current watchlist (DuckDB-backed)."""
    entries = _watchlist_mgr.get_watchlist()
    return {"tickers": entries}


@app.get("/api/watchlist/summary")
async def get_watchlist_summary() -> dict:
    """Aggregate stats for the watchlist header."""
    return _watchlist_mgr.get_summary()


@app.post("/api/watchlist/add")
async def add_to_watchlist(req: WatchlistAddRequest) -> dict:
    """Add a ticker to the watchlist."""
    return _watchlist_mgr.add_ticker(
        ticker=req.ticker,
        source=req.source,
        notes=req.notes,
    )


@app.delete("/api/watchlist/remove/{ticker}")
async def remove_from_watchlist(ticker: str) -> dict:
    """Remove a ticker from the watchlist."""
    return _watchlist_mgr.remove_ticker(ticker)


@app.post("/api/watchlist/import-discovery")
async def import_discovery_to_watchlist(req: WatchlistImportRequest) -> dict:
    """Pull top-scoring discovered tickers into the watchlist."""
    return _watchlist_mgr.import_from_discovery(
        min_score=req.min_score,
        max_tickers=req.max_tickers,
    )


@app.post("/api/watchlist/analyze/{ticker}")
async def analyze_watchlist_ticker(ticker: str) -> dict:
    """Run full analysis pipeline on one watchlist ticker."""
    return await _watchlist_mgr.analyze_ticker(ticker)


@app.post("/api/watchlist/analyze-all")
async def analyze_all_watchlist(
    batch_size: int = Query(default=2, ge=1, le=5),
) -> dict:
    """Analyze all active watchlist tickers in parallel batches."""
    return await _watchlist_mgr.analyze_all(batch_size=batch_size)


@app.post("/api/watchlist/clear")
async def clear_watchlist() -> dict:
    """Clear all watchlist entries."""
    return _watchlist_mgr.clear()


@app.put("/api/watchlist")
async def update_watchlist_legacy(req: WatchlistUpdateRequest) -> dict:
    """Legacy: update watchlist from JSON file (kept for backwards compat)."""
    wl_path = settings.USER_CONFIG_DIR / "watchlist.json"
    data = {"tickers": [t.upper().strip() for t in req.tickers if t.strip()]}
    wl_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.info("Legacy watchlist updated: %s", data["tickers"])
    return data


# ══════════════════════════════════════════════════════════════════════
# DEEP ANALYSIS ROUTES (4-Layer Funnel)
# ══════════════════════════════════════════════════════════════════════


@app.post("/api/analysis/deep/{ticker}")
async def deep_analyze_ticker(ticker: str) -> dict:
    """Run the full 4-layer analysis funnel for one ticker."""
    dossier = await _deep_analysis.analyze_ticker(ticker.upper())
    return {
        "status": "complete",
        "ticker": dossier.ticker,
        "conviction_score": dossier.conviction_score,
        "executive_summary": dossier.executive_summary,
        "bull_case": dossier.bull_case,
        "bear_case": dossier.bear_case,
        "key_catalysts": dossier.key_catalysts,
        "signal_summary": dossier.signal_summary,
        "flags": dossier.quant_scorecard.flags,
        "total_tokens": dossier.total_tokens,
    }


@app.post("/api/analysis/deep-batch")
async def deep_analyze_batch(
    batch_size: int = Query(default=2, ge=1, le=5),
) -> dict:
    """Run deep analysis for all active watchlist tickers."""
    tickers = _watchlist_mgr.get_active_tickers()
    if not tickers:
        return {"status": "no_tickers", "results": []}
    dossiers = await _deep_analysis.analyze_batch(tickers, concurrency=batch_size)
    return {
        "status": "complete",
        "analyzed": len(dossiers),
        "total": len(tickers),
        "results": [
            {
                "ticker": d.ticker,
                "conviction_score": d.conviction_score,
                "signal_summary": d.signal_summary,
            }
            for d in dossiers
        ],
    }


@app.get("/api/dossiers/{ticker}")
async def get_dossier(ticker: str) -> dict:
    """Get the latest dossier for a ticker."""
    result = DeepAnalysisService.get_latest_dossier(ticker.upper())
    if not result:
        raise HTTPException(status_code=404, detail=f"No dossier found for {ticker}")
    return result


@app.get("/api/scorecards/{ticker}")
async def get_scorecard(ticker: str) -> dict:
    """Get the latest quant scorecard for a ticker."""
    result = DeepAnalysisService.get_latest_scorecard(ticker.upper())
    if not result:
        raise HTTPException(status_code=404, detail=f"No scorecard found for {ticker}")
    return result


@app.get("/api/strategy")
async def get_strategy() -> dict:
    """Get the current user trading strategy."""
    strat_path = settings.USER_CONFIG_DIR / "strategy.md"
    if strat_path.exists():
        text = strat_path.read_text(encoding="utf-8")
        return {"strategy": text}
    return {"strategy": ""}


@app.put("/api/strategy")
async def update_strategy(req: StrategyUpdateRequest) -> dict:
    """Update the user trading strategy."""
    strat_path = settings.USER_CONFIG_DIR / "strategy.md"
    strat_path.write_text(req.strategy_text, encoding="utf-8")
    logger.info("Strategy updated (%d chars)", len(req.strategy_text))
    return {"status": "updated", "length": len(req.strategy_text)}


@app.get("/api/risk-params")
async def get_risk_params() -> dict:
    """Get the current risk parameters."""
    rp_path = settings.USER_CONFIG_DIR / "risk_params.json"
    if rp_path.exists():
        return json.loads(rp_path.read_text(encoding="utf-8"))
    return {}


@app.put("/api/risk-params")
async def update_risk_params(req: RiskParamsUpdateRequest) -> dict:
    """Update risk parameters."""
    rp_path = settings.USER_CONFIG_DIR / "risk_params.json"
    rp_path.write_text(json.dumps(req.params, indent=2), encoding="utf-8")
    logger.info("Risk params updated")
    return {"status": "updated"}


# ══════════════════════════════════════════════════════════════════════
# LLM CONFIGURATION API
# ══════════════════════════════════════════════════════════════════════


@app.get("/api/llm-config")
async def get_llm_config() -> dict:
    """Return the current LLM configuration."""
    return settings.get_llm_config()


@app.put("/api/llm-config")
async def update_llm_config(req: LLMConfigRequest) -> dict:
    """Save new LLM settings + hot-patch the running config.

    For LM Studio: also reloads the model via /api/v1/models/load
    with the new context_length, and returns the verified config
    that LM Studio actually applied.
    """
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    merged = settings.update_llm_config(data)
    logger.info(
        "LLM config updated: provider=%s model=%s ctx=%s",
        merged.get("provider"),
        merged.get("model"),
        merged.get("context_size"),
    )

    result: dict = {"status": "updated", "config": merged}

    # ── LM Studio: reload model with new context_length ──────────
    provider = merged.get("provider", "")
    if provider == "lmstudio":
        lms_url = merged.get("lmstudio_url", "http://localhost:1234").rstrip("/")
        model_id = merged.get("model", "")
        ctx = merged.get("context_size", 8192)

        if model_id:
            import httpx as _httpx
            load_payload = {
                "model": model_id,
                "context_length": ctx,
                "echo_load_config": True,
            }
            try:
                async with _httpx.AsyncClient(timeout=60.0) as client:
                    # ── Step 1: Unload current model to avoid stacking ──
                    try:
                        await client.post(
                            f"{lms_url}/api/v1/models/unload",
                            json={"instance_id": model_id},
                        )
                        logger.info(
                            "[LM Studio] Unloaded %s before reload", model_id,
                        )
                    except Exception:
                        # Model might not be loaded yet — that's fine
                        pass

                    # ── Step 2: Load with new config ──
                    resp = await client.post(
                        f"{lms_url}/api/v1/models/load",
                        json=load_payload,
                    )
                    resp.raise_for_status()
                    load_result = resp.json()

                # Extract the verified config LM Studio actually applied
                load_config = load_result.get("load_config", {})
                actual_ctx = load_config.get("context_length")
                load_time = load_result.get("load_time_seconds")

                result["lmstudio_verified"] = {
                    "status": "model_reloaded",
                    "model": model_id,
                    "requested_context_length": ctx,
                    "actual_context_length": actual_ctx,
                    "context_match": actual_ctx == ctx if actual_ctx else None,
                    "load_time_seconds": load_time,
                    "full_load_config": load_config,
                }
                logger.info(
                    "[LM Studio] Model reloaded: ctx requested=%d, "
                    "actual=%s, load_time=%.1fs",
                    ctx, actual_ctx, load_time or 0,
                )
            except Exception as exc:
                result["lmstudio_verified"] = {
                    "status": "reload_failed",
                    "error": str(exc),
                    "note": (
                        "Config was saved but LM Studio model reload failed. "
                        "You may need to reload the model manually in LM Studio."
                    ),
                }
                logger.warning(
                    "[LM Studio] Model reload failed: %s", exc,
                )

    return result


@app.get("/api/llm-models")
async def get_llm_models(
    provider: str = Query(default=None),
    url: str = Query(default=None),
) -> dict:
    """Fetch available models from the configured (or specified) LLM provider.

    Query params let the frontend test arbitrary URLs before saving.
    """
    _provider = provider or settings.LLM_PROVIDER
    if url:
        _url = url
    elif _provider == "lmstudio":
        _url = settings.LMSTUDIO_URL
    else:
        _url = settings.OLLAMA_URL

    models = await LLMService.fetch_models(_provider, _url)
    return {
        "provider": _provider,
        "url": _url,
        "models": models,
        "connected": len(models) > 0,
    }


# ══════════════════════════════════════════════════════════════════════
# LIVE QUOTES — fast price via yfinance.fast_info (no pipeline needed)
# ══════════════════════════════════════════════════════════════════════


def _fetch_one_quote(symbol: str) -> dict:
    """Fetch live quote for a single ticker via yfinance.Ticker.fast_info.

    fast_info is a lightweight call — no full .info download required.
    Returns a dict with price, change, change_pct, market_cap, volume.
    """
    import yfinance as yf  # lazy import to keep startup fast

    try:
        t = yf.Ticker(symbol)
        fi = t.fast_info
        price = getattr(fi, "last_price", None)
        prev = getattr(fi, "previous_close", None)
        mcap = getattr(fi, "market_cap", None)
        vol = getattr(fi, "last_volume", None)

        change = None
        change_pct = None
        if price is not None and prev is not None and prev != 0:
            change = round(price - prev, 4)
            change_pct = round((change / prev) * 100, 4)

        return {
            "ticker": symbol,
            "price": round(price, 2) if price is not None else None,
            "prev_close": round(prev, 2) if prev is not None else None,
            "change": change,
            "change_pct": change_pct,
            "market_cap": mcap,
            "volume": vol,
        }
    except Exception as e:
        logger.warning("Quote fetch failed for %s: %s", symbol, e)
        return {"ticker": symbol, "price": None, "error": str(e)}


@app.get("/api/quotes")
async def get_quotes(
    tickers: str = Query(..., description="Comma-separated ticker symbols"),
) -> dict:
    """Batch live-price endpoint — fetches current prices from Yahoo Finance.

    Uses yfinance.Ticker.fast_info for each ticker in parallel.
    Much faster than running the full pipeline, designed for watchlist display.
    """
    symbols = [s.strip().upper() for s in tickers.split(",") if s.strip()]
    if not symbols:
        return {"quotes": {}}

    # Cap at 20 tickers to avoid abuse
    symbols = symbols[:20]

    import concurrent.futures

    # Run yfinance calls in thread pool (they're synchronous I/O)
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(symbols), 8)
    ) as pool:
        futures = [loop.run_in_executor(pool, _fetch_one_quote, s) for s in symbols]
        results = await asyncio.gather(*futures, return_exceptions=True)

    quotes = {}
    for result in results:
        if isinstance(result, Exception):
            continue
        if isinstance(result, dict) and "ticker" in result:
            quotes[result["ticker"]] = result

    return {"quotes": quotes}


# ══════════════════════════════════════════════════════════════════════
# DASHBOARD DATA API — DuckDB queries for frontend
# ══════════════════════════════════════════════════════════════════════


@app.get("/api/dashboard/overview/{ticker}")
async def dashboard_overview(ticker: str) -> dict:
    """Consolidated overview: latest price, fundamentals, key technicals."""
    ticker = ticker.upper().strip()
    try:
        # Latest price
        prices = _query_to_dicts(
            "SELECT * FROM price_history WHERE ticker = ? ORDER BY date DESC LIMIT 5",
            [ticker],
        )
        latest_price = prices[0] if prices else {}

        # Previous close for change calculation
        prev_price = prices[1] if len(prices) > 1 else {}

        # Latest fundamentals
        fundas = _query_to_dicts(
            "SELECT * FROM fundamentals WHERE ticker = ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            [ticker],
        )

        # Latest technicals
        techs = _query_to_dicts(
            "SELECT * FROM technicals WHERE ticker = ? ORDER BY date DESC LIMIT 1",
            [ticker],
        )

        # Latest risk metrics
        risks = _query_to_dicts(
            "SELECT * FROM risk_metrics WHERE ticker = ? "
            "ORDER BY computed_date DESC LIMIT 1",
            [ticker],
        )

        # News count
        news_count = _query_to_dicts(
            "SELECT COUNT(*) as cnt FROM news_articles WHERE ticker = ?",
            [ticker],
        )

        # Analyst data
        analyst = _query_to_dicts(
            "SELECT * FROM analyst_data WHERE ticker = ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            [ticker],
        )

        return {
            "ticker": ticker,
            "price": latest_price,
            "prev_price": prev_price,
            "fundamentals": fundas[0] if fundas else {},
            "technicals": techs[0] if techs else {},
            "risk_metrics": risks[0] if risks else {},
            "news_count": news_count[0]["cnt"] if news_count else 0,
            "analyst": analyst[0] if analyst else {},
        }
    except Exception as e:
        logger.error("Dashboard overview error: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/dashboard/prices/{ticker}")
async def dashboard_prices(
    ticker: str,
    days: int = Query(default=365, ge=1, le=3650),
) -> dict:
    """OHLCV price history for charts."""
    ticker = ticker.upper().strip()
    try:
        rows = _query_to_dicts(
            "SELECT date, open, high, low, close, volume "
            "FROM price_history WHERE ticker = ? "
            "ORDER BY date DESC LIMIT ?",
            [ticker, days],
        )
        rows.reverse()  # Oldest first for chart rendering
        return {"ticker": ticker, "count": len(rows), "prices": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/dashboard/technicals/{ticker}")
async def dashboard_technicals(ticker: str) -> dict:
    """Latest technical indicators."""
    ticker = ticker.upper().strip()
    try:
        rows = _query_to_dicts(
            "SELECT * FROM technicals WHERE ticker = ? ORDER BY date DESC LIMIT 30",
            [ticker],
        )
        return {"ticker": ticker, "count": len(rows), "technicals": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/dashboard/news/{ticker}")
async def dashboard_news(ticker: str) -> dict:
    """Recent news articles from DB."""
    ticker = ticker.upper().strip()
    try:
        rows = _query_to_dicts(
            "SELECT title, publisher, url, published_at, summary, source "
            "FROM news_articles WHERE ticker = ? "
            "ORDER BY published_at DESC LIMIT 30",
            [ticker],
        )
        return {"ticker": ticker, "count": len(rows), "articles": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/dashboard/youtube/{ticker}")
async def dashboard_youtube(ticker: str) -> dict:
    """YouTube transcripts from DB."""
    ticker = ticker.upper().strip()
    try:
        rows = _query_to_dicts(
            "SELECT video_id, title, channel, published_at, "
            "duration_seconds, raw_transcript, "
            "LENGTH(raw_transcript) as transcript_length "
            "FROM youtube_transcripts WHERE ticker = ? "
            "ORDER BY published_at DESC LIMIT 20",
            [ticker],
        )
        return {"ticker": ticker, "count": len(rows), "videos": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/dashboard/analysis/{ticker}")
async def dashboard_cached_analysis(ticker: str) -> dict:
    """Load the most recent saved analysis reports from disk.

    Returns cached agent reports + decision if they exist, so the frontend
    can display them instantly without re-running the LLM pipeline.
    """
    ticker = ticker.upper().strip()
    report_base = settings.REPORTS_DIR / ticker
    if not report_base.exists():
        return {"ticker": ticker, "cached": False}

    # Find the most recent date folder
    date_dirs = sorted(
        [d for d in report_base.iterdir() if d.is_dir()],
        key=lambda d: d.name,
        reverse=True,
    )
    if not date_dirs:
        return {"ticker": ticker, "cached": False}

    latest = date_dirs[0]
    report_files = {
        "technical": "technical_report.json",
        "fundamental": "fundamental_report.json",
        "sentiment": "sentiment_report.json",
        "risk": "risk_report.json",
        "decision": "final_decision.json",
        "pooled": "pooled_analysis.json",
    }

    agents: dict = {}
    decision = None
    for key, filename in report_files.items():
        fpath = latest / filename
        if fpath.exists():
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                if key == "decision":
                    decision = data
                elif key != "pooled":
                    agents[key] = {"status": "ok", "report": data}
            except (json.JSONDecodeError, OSError):
                pass

    if not agents and decision is None:
        return {"ticker": ticker, "cached": False}

    return {
        "ticker": ticker,
        "cached": True,
        "date": latest.name,
        "agents": agents,
        "decision": decision,
    }


@app.get("/api/dashboard/financials/{ticker}")
async def dashboard_financials(ticker: str) -> dict:
    """Financial history + balance sheet + cash flows."""
    ticker = ticker.upper().strip()
    try:
        income = _query_to_dicts(
            "SELECT * FROM financial_history WHERE ticker = ? ORDER BY year",
            [ticker],
        )
        balance = _query_to_dicts(
            "SELECT * FROM balance_sheet WHERE ticker = ? ORDER BY year",
            [ticker],
        )
        cashflow = _query_to_dicts(
            "SELECT * FROM cash_flows WHERE ticker = ? ORDER BY year",
            [ticker],
        )
        return {
            "ticker": ticker,
            "income_statement": income,
            "balance_sheet": balance,
            "cash_flows": cashflow,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/dashboard/risk/{ticker}")
async def dashboard_risk(ticker: str) -> dict:
    """Risk metrics from DB."""
    ticker = ticker.upper().strip()
    try:
        rows = _query_to_dicts(
            "SELECT * FROM risk_metrics WHERE ticker = ? "
            "ORDER BY computed_date DESC LIMIT 1",
            [ticker],
        )
        return {"ticker": ticker, "metrics": rows[0] if rows else {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/dashboard/analyst/{ticker}")
async def dashboard_analyst(ticker: str) -> dict:
    """Analyst data + insider activity + earnings calendar."""
    ticker = ticker.upper().strip()
    try:
        analyst = _query_to_dicts(
            "SELECT * FROM analyst_data WHERE ticker = ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            [ticker],
        )
        insider = _query_to_dicts(
            "SELECT * FROM insider_activity WHERE ticker = ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            [ticker],
        )
        earnings = _query_to_dicts(
            "SELECT * FROM earnings_calendar WHERE ticker = ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            [ticker],
        )
        return {
            "ticker": ticker,
            "analyst": analyst[0] if analyst else {},
            "insider": insider[0] if insider else {},
            "earnings": earnings[0] if earnings else {},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/dashboard/db-stats")
async def dashboard_db_stats() -> dict:
    """Database row counts for diagnostics."""
    tables = [
        "price_history",
        "fundamentals",
        "financial_history",
        "technicals",
        "news_articles",
        "youtube_transcripts",
        "risk_metrics",
        "balance_sheet",
        "cash_flows",
        "analyst_data",
        "insider_activity",
        "earnings_calendar",
        "discovered_tickers",
        "ticker_scores",
        "watchlist",
        "positions",
        "orders",
        "price_triggers",
        "portfolio_snapshots",
    ]
    counts = {}
    db = get_db()
    for table in tables:
        try:
            result = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
            counts[table] = result[0] if result else 0
        except Exception:
            counts[table] = -1  # Table doesn't exist
    return {"counts": counts}


# ══════════════════════════════════════════════════════════════════════
# TRADING ENGINE API (Phase 3 — Paper Trading)
# ══════════════════════════════════════════════════════════════════════

from app.services.paper_trader import PaperTrader  # noqa: E402
from app.services.price_monitor import PriceMonitor  # noqa: E402

_paper_trader = PaperTrader()
_price_monitor = PriceMonitor(_paper_trader)


@app.get("/api/portfolio")
async def get_portfolio() -> dict:
    """Current cash + positions + total value."""
    return _paper_trader.get_portfolio()


@app.get("/api/portfolio/history")
async def get_portfolio_history(
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict:
    """Portfolio snapshots over time (equity curve data)."""
    snapshots = _paper_trader.get_portfolio_history(limit=limit)
    return {"count": len(snapshots), "snapshots": snapshots}


@app.get("/api/positions")
async def get_positions() -> dict:
    """All open positions with entry details."""
    positions = _paper_trader.get_positions()
    return {"count": len(positions), "positions": positions}


@app.post("/api/positions/{ticker}/close")
async def close_position(ticker: str) -> dict:
    """Manually close a position at current market price."""
    ticker = ticker.upper().strip()
    quote = _fetch_one_quote(ticker)
    price = quote.get("price")
    if not price:
        raise HTTPException(
            status_code=400, detail=f"Could not fetch price for {ticker}"
        )

    positions = _paper_trader.get_positions()
    qty = 0
    for p in positions:
        if p["ticker"] == ticker:
            qty = p["qty"]
            break

    if qty <= 0:
        raise HTTPException(status_code=404, detail=f"No open position for {ticker}")

    order = _paper_trader.sell(
        ticker=ticker,
        qty=qty,
        price=price,
        signal="MANUAL_CLOSE",
    )
    if not order:
        raise HTTPException(status_code=500, detail="Sell order failed")

    return {
        "status": "closed",
        "ticker": ticker,
        "qty": order.qty,
        "price": order.price,
        "order_id": order.id,
    }


@app.get("/api/orders")
async def get_orders(
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """Order history."""
    orders = _paper_trader.get_orders(limit=limit)
    return {"count": len(orders), "orders": orders}


@app.get("/api/triggers")
async def get_triggers() -> dict:
    """Active price triggers (stop-loss, take-profit, trailing)."""
    triggers = _paper_trader.get_triggers()
    return {"count": len(triggers), "triggers": triggers}


@app.post("/api/trading/check-triggers")
async def check_triggers() -> dict:
    """Manually fire trigger check against current prices."""
    actions = await _price_monitor.check_triggers()
    return {
        "triggered": len(actions),
        "actions": actions,
    }


@app.post("/api/portfolio/reset")
async def reset_portfolio(req: PortfolioResetRequest | None = None) -> dict:
    """Reset the portfolio to a specified balance.

    Clears all positions, orders, triggers, and snapshots.
    If balance is provided, uses that. Otherwise reads from risk_params.json.
    """
    balance = req.balance if req and req.balance else None
    result = _paper_trader.reset_portfolio(new_balance=balance)
    return result


# ══════════════════════════════════════════════════════════════════════
# DISCOVERY API (Phase 12 — Ticker Discovery)
# ══════════════════════════════════════════════════════════════════════

from app.services.discovery_service import DiscoveryService  # noqa: E402

_discovery = DiscoveryService()


@app.get("/api/discovery/run")
async def run_discovery(
    reddit: bool = Query(default=True),
    youtube: bool = Query(default=True),
    hours: int = Query(default=24),
    max_tickers: int = Query(
        default=0, description="Cap results to N tickers (0=no limit)"
    ),
) -> dict:
    """Trigger a discovery scan (Reddit + YouTube)."""
    logger.info(
        "[API] /api/discovery/run called (reddit=%s, youtube=%s, max=%s)",
        reddit,
        youtube,
        max_tickers,
    )
    result = await _discovery.run_discovery(
        enable_reddit=reddit,
        enable_youtube=youtube,
        youtube_hours=hours,
        max_tickers=max_tickers if max_tickers > 0 else None,
    )
    return {
        "status": "complete",
        "tickers": [t.model_dump() for t in result.tickers],
        "reddit_count": result.reddit_count,
        "youtube_count": result.youtube_count,
        "transcript_count": result.transcript_count,
        "duration_seconds": round(result.duration_seconds, 1),
    }


@app.get("/api/discovery/results")
async def get_discovery_results(
    limit: int = Query(default=20),
) -> dict:
    """Get latest scored tickers from the aggregated table."""
    scores = _discovery.get_latest_scores(limit=limit)
    return {"scores": scores}


@app.get("/api/discovery/history")
async def get_discovery_history(
    limit: int = Query(default=50),
) -> dict:
    """Get raw discovery history with timestamps."""
    history = _discovery.get_discovery_history(limit=limit)
    return {"history": history}


@app.get("/api/discovery/status")
async def get_discovery_status() -> dict:
    """Bot vitals: running state, last run, aggregate stats."""
    return _discovery.status()


@app.post("/api/discovery/clear")
async def clear_discovery_data() -> dict:
    """Clear all discovery data (discovered_tickers + ticker_scores)."""
    return _discovery.clear_data()


@app.get("/api/discovery/transcripts/{ticker}")
async def get_discovery_transcripts(ticker: str) -> dict:
    """Lightweight transcript metadata for a specific ticker.

    Returns title, channel, duration, and a preview snippet
    (first 200 chars) — NOT the full raw transcript.
    """
    ticker = ticker.upper().strip()
    try:
        rows = _query_to_dicts(
            """
            SELECT video_id, title, channel, published_at,
                   duration_seconds,
                   SUBSTRING(raw_transcript, 1, 200) AS preview,
                   LENGTH(raw_transcript) AS transcript_length
            FROM youtube_transcripts
            WHERE ticker = ?
            ORDER BY collected_at DESC
            LIMIT 10
            """,
            [ticker],
        )
        return {"ticker": ticker, "count": len(rows), "transcripts": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ══════════════════════════════════════════════════════════════════════
# PIPELINE ACTIVITY LOG — persistent event audit trail
# ══════════════════════════════════════════════════════════════════════


@app.get("/api/pipeline/events")
async def get_pipeline_events(
    limit: int = Query(default=200, ge=1, le=1000),
    phase: str | None = Query(default=None),
    ticker: str | None = Query(default=None),
    loop_id: str | None = Query(default=None),
) -> dict:
    """Get pipeline events with optional filtering."""
    db = get_db()
    conditions: list[str] = []
    params: list = []

    if phase:
        conditions.append("phase = ?")
        params.append(phase)
    if ticker:
        conditions.append("ticker = ?")
        params.append(ticker.upper())
    if loop_id:
        conditions.append("loop_id = ?")
        params.append(loop_id)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    rows = db.execute(
        f"""
        SELECT id, timestamp, phase, event_type, ticker,
               detail, metadata, loop_id, status
        FROM pipeline_events
        {where}
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    events = [
        {
            "id": r[0],
            "timestamp": str(r[1]) if r[1] else None,
            "phase": r[2],
            "event_type": r[3],
            "ticker": r[4],
            "detail": r[5],
            "metadata": r[6],
            "loop_id": r[7],
            "status": r[8],
        }
        for r in rows
    ]
    return {"count": len(events), "events": events}


# ══════════════════════════════════════════════════════════════════════
# BOT CONTROL API — Autonomous Full Loop
# ══════════════════════════════════════════════════════════════════════

from app.services.autonomous_loop import AutonomousLoop  # noqa: E402

_loop = AutonomousLoop()
_loop_task: asyncio.Task | None = None


@app.post("/api/bot/run-loop")
async def run_full_loop(max_tickers: int = 10) -> dict:
    """Trigger the full autonomous loop: Discovery → Import → Deep Analysis."""
    global _loop, _loop_task  # noqa: PLW0603

    if _loop._state["running"]:
        raise HTTPException(status_code=409, detail="Loop is already running")

    # Re-create the loop with the requested max_tickers
    _loop = AutonomousLoop(max_tickers=max_tickers)

    async def _run() -> None:
        try:
            await _loop.run_full_loop()
        except Exception:
            logger.exception("[AutoLoop] Unhandled error in background loop")

    _loop_task = asyncio.create_task(_run())
    return {
        "status": "started",
        "message": f"Full loop is running (max_tickers={max_tickers})",
    }


@app.get("/api/bot/loop-status")
async def get_loop_status() -> dict:
    """Poll the current state of the autonomous loop."""
    return _loop.get_status()


# ── Individual phase endpoints (developer debugging) ──────────────


@app.post("/api/bot/run-discovery")
async def run_discovery_phase() -> dict:
    """Run ONLY Phase 1: Ticker Discovery (Reddit + YouTube scanning)."""
    if _loop._state.get("running"):
        raise HTTPException(status_code=409, detail="Loop is already running")

    _loop._reset_state()

    async def _run() -> None:
        try:
            result = await _loop._run_phase(
                "discovery",
                "Scanning Reddit + YouTube for tickers…",
                _loop._do_discovery,
            )
            _loop._state["running"] = False
            _loop._state["phase"] = "done"
            _loop._log(f"Discovery phase completed: {result.get('status')}")
        except Exception:
            _loop._state["running"] = False
            logger.exception("[AutoLoop] Discovery phase error")

    asyncio.create_task(_run())
    return {"status": "started", "phase": "discovery"}


@app.post("/api/bot/run-import")
async def run_import_phase() -> dict:
    """Run ONLY Phase 2: Auto-Import top tickers to watchlist."""
    if _loop._state.get("running"):
        raise HTTPException(status_code=409, detail="Loop is already running")

    _loop._reset_state()

    async def _run() -> None:
        try:
            result = await _loop._run_phase(
                "import",
                "Importing top tickers to watchlist…",
                _loop._do_import,
            )
            _loop._state["running"] = False
            _loop._state["phase"] = "done"
            _loop._log(f"Import phase completed: {result.get('status')}")
        except Exception:
            _loop._state["running"] = False
            logger.exception("[AutoLoop] Import phase error")

    asyncio.create_task(_run())
    return {"status": "started", "phase": "import"}


@app.post("/api/bot/run-analysis")
async def run_analysis_phase() -> dict:
    """Run ONLY Phase 3: Deep Analysis on all active watchlist tickers."""
    if _loop._state.get("running"):
        raise HTTPException(status_code=409, detail="Loop is already running")

    _loop._reset_state()

    async def _run() -> None:
        try:
            result = await _loop._run_phase(
                "analysis",
                "Running 4-layer deep analysis on all active tickers…",
                _loop._do_deep_analysis,
            )
            _loop._state["running"] = False
            _loop._state["phase"] = "done"
            _loop._log(f"Analysis phase completed: {result.get('status')}")
        except Exception:
            _loop._state["running"] = False
            logger.exception("[AutoLoop] Analysis phase error")

    asyncio.create_task(_run())
    return {"status": "started", "phase": "analysis"}


@app.post("/api/bot/run-trading")
async def run_trading_phase() -> dict:
    """Run ONLY Phase 4: Signal routing + paper trading."""
    if _loop._state.get("running"):
        raise HTTPException(status_code=409, detail="Loop is already running")

    _loop._reset_state()

    async def _run() -> None:
        try:
            result = await _loop._run_phase(
                "trading",
                "Processing signals through paper trader…",
                _loop._do_trading,
            )
            _loop._state["running"] = False
            _loop._state["phase"] = "done"
            _loop._log(f"Trading phase completed: {result.get('status')}")
        except Exception:
            _loop._state["running"] = False
            logger.exception("[AutoLoop] Trading phase error")

    asyncio.create_task(_run())
    return {"status": "started", "phase": "trading"}


# ══════════════════════════════════════════════════════════════════════
# AUTONOMOUS SCHEDULER API (Phase 4)
# ══════════════════════════════════════════════════════════════════════

from app.services.scheduler import TradingScheduler  # noqa: E402

_scheduler = TradingScheduler(
    autonomous_loop=_loop,
    price_monitor=_price_monitor,
)


@app.on_event("startup")
async def _auto_start_scheduler() -> None:
    """Auto-start the trading scheduler when the server boots."""
    result = _scheduler.start()
    logger.info("[Boot] Scheduler auto-started: %s", result)


@app.post("/api/scheduler/start")
async def scheduler_start() -> dict:
    """Start the automated daily trading schedule."""
    return _scheduler.start()


@app.post("/api/scheduler/stop")
async def scheduler_stop() -> dict:
    """Kill switch — stop all scheduled jobs."""
    return _scheduler.stop()


@app.get("/api/scheduler/status")
async def scheduler_status() -> dict:
    """Get scheduler state: running, jobs, market status."""
    return _scheduler.get_status()


@app.post("/api/scheduler/run/{job_name}")
async def scheduler_run_job(job_name: str) -> dict:
    """Manually trigger a scheduler job: pre_market, midday, end_of_day."""
    return await _scheduler.run_job(job_name)


@app.get("/api/scheduler/history")
async def scheduler_history(
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """Recent scheduler run history from DB."""
    history = _scheduler.get_history(limit=limit)
    return {"count": len(history), "history": history}


# ── Reports ────────────────────────────────────────────────────────

from app.services.report_generator import ReportGenerator  # noqa: E402

_report_gen = ReportGenerator()


@app.get("/api/reports/latest")
async def get_latest_reports() -> dict:
    """Get the most recent pre-market and EOD reports."""
    return _report_gen.get_latest()


@app.get("/api/reports/history")
async def get_report_history(
    limit: int = Query(default=10, ge=1, le=50),
    report_type: str | None = Query(default=None),
) -> dict:
    """Get report history with optional type filter."""
    db = get_db()
    conditions: list[str] = []
    params: list = []

    if report_type:
        conditions.append("report_type = ?")
        params.append(report_type)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    rows = db.execute(
        f"""
        SELECT id, report_type, report_date, content, created_at
        FROM reports
        {where}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    reports = [
        {
            "id": r[0],
            "report_type": r[1],
            "report_date": str(r[2]),
            "content": json.loads(r[3]) if r[3] else {},
            "created_at": str(r[4]) if r[4] else None,
        }
        for r in rows
    ]
    return {"count": len(reports), "reports": reports}


# ── Market Status (standalone) ─────────────────────────────────────

from app.utils.market_hours import market_status as _market_status  # noqa: E402


@app.get("/api/market/status")
async def get_market_status() -> dict:
    """Current NYSE market status (open/closed, countdown)."""
    return _market_status()

