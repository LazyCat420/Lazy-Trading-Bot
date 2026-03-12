"""FastAPI application — API endpoints + frontend dashboard."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path

import httpx
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
    ollama_url: str | None = None
    model: str | None = None
    context_size: int | None = None
    temperature: float | None = None
    # RAG / Embedding settings
    embedding_model: str | None = None
    rag_enabled: bool | None = None
    rag_top_k: int | None = None
    rag_max_chars: int | None = None


# ── Singleton services ──────────────────────────────────────────────
pipeline = PipelineService()

# Lazy import to avoid circular — WatchlistManager uses PipelineService
from app.services.deep_analysis_service import DeepAnalysisService  # noqa: E402
from app.services.watchlist_manager import WatchlistManager  # noqa: E402

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


# ── Exclusion / Scoreboard Delete API ─────────────────────────────

@app.delete("/api/scoreboard/{symbol}")
async def delete_from_scoreboard(symbol: str) -> dict:
    """Remove a symbol from scoreboard + watchlist, persist exclusion."""
    from app.services.symbol_filter import UserExclusionsService

    symbol = symbol.upper().strip()
    svc = UserExclusionsService()
    svc.exclude(symbol, bot_id="default", reason="user_deleted")

    db = get_db()
    # Remove from watchlist
    db.execute(
        "UPDATE watchlist SET status = 'removed' "
        "WHERE ticker = ? AND status = 'active'",
        [symbol],
    )
    # Remove from ticker_scores
    db.execute(
        "DELETE FROM ticker_scores WHERE ticker = ?", [symbol],
    )
    db.commit()
    logger.info("[Scoreboard] Deleted + excluded %s", symbol)
    return {"status": "excluded", "symbol": symbol}


@app.get("/api/exclusions")
async def list_exclusions() -> dict:
    """List all user-excluded symbols."""
    from app.services.symbol_filter import UserExclusionsService

    svc = UserExclusionsService()
    exclusions = svc.list_exclusions(bot_id="default")
    return {"exclusions": exclusions, "count": len(exclusions)}


@app.post("/api/exclusions/{symbol}/restore")
async def restore_exclusion(symbol: str) -> dict:
    """Restore a previously excluded symbol."""
    from app.services.symbol_filter import UserExclusionsService

    svc = UserExclusionsService()
    found = svc.restore(symbol.upper().strip(), bot_id="default")
    if found:
        return {"status": "restored", "symbol": symbol.upper().strip()}
    return {"status": "not_found", "symbol": symbol.upper().strip()}


# ── Circuit Breaker API ──────────────────────────────────────────

@app.get("/api/circuit-breaker/{bot_id}")
async def get_circuit_breaker_status(bot_id: str = "default") -> dict:
    """Get circuit breaker status for a bot."""
    from app.services.circuit_breaker import CircuitBreaker

    return CircuitBreaker.get_status(bot_id)


@app.post("/api/circuit-breaker/{bot_id}/reset")
async def reset_circuit_breaker(bot_id: str = "default") -> dict:
    """Manually reset the circuit breaker for a bot."""
    from app.services.circuit_breaker import CircuitBreaker

    return CircuitBreaker.reset(bot_id)


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


@app.get("/api/llm/vram-estimate")
async def get_vram_estimate(model: str = "") -> dict:
    """Return VRAM estimation data for the frontend slider.

    Queries Ollama's /api/show + /api/tags for model architecture
    and config for total GPU memory.  Returns everything needed for
    clientside VRAM prediction — NO model loading involved.
    """
    from app.config import settings as _cfg

    target_model = model or _cfg.LLM_MODEL
    base_url = _cfg.OLLAMA_URL.rstrip("/")

    # Total GPU memory from config (SYSTEM_TOTAL_VRAM_GB)
    total_bytes = LLMService.get_total_vram_bytes()
    total_gb = round(total_bytes / (1024**3), 1) if total_bytes else 0

    result: dict = {
        "total_vram_gb": total_gb,
        "model_weight_gb": 0,
        "kv_bytes_per_token": 0,
        "model_max_ctx": 0,
        "model_found": False,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Get model file size from /api/tags
            tags_resp = await client.get(f"{base_url}/api/tags")
            tags_resp.raise_for_status()
            model_file_size = 0
            for m in tags_resp.json().get("models", []):
                if (
                    m["name"] == target_model
                    or m["name"].split(":")[0]
                    == target_model.split(":")[0]
                ):
                    model_file_size = m.get("size", 0)
                    break

            if not model_file_size:
                return result

            result["model_found"] = True
            result["model_weight_gb"] = round(
                model_file_size / (1024**3), 2,
            )

            # Get architecture from /api/show
            show_resp = await client.post(
                f"{base_url}/api/show",
                json={"name": target_model},
            )
            show_resp.raise_for_status()
            model_info = show_resp.json().get("model_info", {})

            # Max context
            for key, val in model_info.items():
                if "context_length" in key and isinstance(val, int):
                    result["model_max_ctx"] = val
                    break

            # KV rate from architecture
            est = LLMService.estimate_model_vram(
                model_info, model_file_size, 1,
            )
            result["kv_bytes_per_token"] = est["kv_bytes_per_token"]

            # Override with cached real kv_rate from previous load
            # (much more accurate — includes Ollama overhead)
            # BUT: sanity-check against Jetson unified-memory inflation
            theoretical_rate = est["kv_bytes_per_token"]
            cached = _cfg.LLM_VRAM_MEASUREMENTS.get(target_model)
            if cached:
                if cached.get("real_kv_rate"):
                    cached_rate = cached["real_kv_rate"]
                    if (
                        theoretical_rate > 0
                        and cached_rate > theoretical_rate * 4.0
                    ):
                        # Inflated — use theoretical × 1.5 instead
                        result["kv_bytes_per_token"] = theoretical_rate * 1.5
                        result["using_cached_rate"] = False
                        result["cache_rejected"] = True
                        logger.warning(
                            "[VRAM] Cached kv_rate %.0f > 4× theoretical "
                            "%.0f — using %.0f (theo×1.5)",
                            cached_rate, theoretical_rate,
                            theoretical_rate * 1.5,
                        )
                    else:
                        result["kv_bytes_per_token"] = cached_rate
                        result["using_cached_rate"] = True

    except Exception as exc:
        logger.warning("[VRAM] Estimate endpoint error: %s", exc)

    return result


@app.put("/api/llm-config")
async def update_llm_config(req: LLMConfigRequest) -> dict:
    """Save new LLM settings + hot-patch the running config.

    Verifies the Ollama model exists and pre-warms it into VRAM.
    """
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    merged = settings.update_llm_config(data)
    logger.info(
        "LLM config updated: model=%s ctx=%s",
        merged.get("model"),
        merged.get("context_size"),
    )

    result: dict = {"status": "updated", "config": merged}

    # ── Auto-register bot for this model ──────────────────────────
    model_id = merged.get("model", "")
    if model_id:
        from app.services.bot_registry import BotRegistry as _BReg

        # Check if a bot already exists for this model
        existing_bots = _BReg.list_bots(include_inactive=True)
        bot_for_model = None
        for b in existing_bots:
            if b.get("model_name") == model_id:
                bot_for_model = b
                break

        if not bot_for_model:
            # Auto-create a bot entry for this model
            _prov_url = merged.get("ollama_url", "http://localhost:11434")
            _ctx = merged.get("context_size", 8192)
            bot_for_model = _BReg.register_bot(
                model_name=model_id,
                display_name=model_id.split("/")[-1] if "/" in model_id else model_id,
                provider="ollama",
                provider_url=_prov_url,
                context_length=_ctx,
                temperature=merged.get("temperature", 0.3),
                top_p=merged.get("top_p", 1.0),
                max_tokens=merged.get("max_tokens", 0),
                eval_batch_size=merged.get("eval_batch_size", 512),
                flash_attention=merged.get("flash_attention", True),
            )
            logger.info(
                "[BotAutoReg] Created bot %s for model %s",
                bot_for_model["bot_id"],
                model_id,
            )
        else:
            # Update existing bot settings
            _ctx = merged.get("context_size", 8192)
            _BReg.update_bot_settings(
                bot_for_model["bot_id"],
                {
                    "context_length": _ctx,
                    "temperature": merged.get("temperature", 0.3),
                    "top_p": merged.get("top_p", 1.0),
                },
            )
            # Re-activate if inactive
            if bot_for_model.get("status") == "inactive":
                db = get_db()
                db.execute(
                    "UPDATE bots SET status = 'active' WHERE bot_id = ?",
                    [bot_for_model["bot_id"]],
                )
                db.commit()

        # Set this as the active bot
        _set_active_bot(bot_for_model["bot_id"])
        result["active_bot_id"] = bot_for_model["bot_id"]
        result["active_bot_name"] = bot_for_model.get("display_name", model_id)

    return result


@app.get("/api/llm-models")
async def get_llm_models(
    url: str = Query(default=None),
) -> dict:
    """Fetch available models.

    Uses Prism /config by default.  If a custom Ollama URL is provided
    (frontend testing), queries that URL directly.
    """
    if url:
        # Direct Ollama query — used when testing a custom URL
        models = await LLMService.fetch_models(url)
        return {
            "provider": "ollama",
            "url": url,
            "models": models,
            "connected": len(models) > 0,
        }

    # Default: fetch from Prism
    models = await LLMService.fetch_models_from_prism()
    return {
        "provider": "ollama",
        "url": settings.PRISM_URL,
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
        "news_full_articles",
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
            result = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = result[0] if result else 0
        except Exception:
            counts[table] = -1  # Table doesn't exist
    return {"counts": counts}


# ══════════════════════════════════════════════════════════════════════
# TRADING ENGINE API (Phase 3 — Paper Trading)
# ══════════════════════════════════════════════════════════════════════

from app.services.paper_trader import PaperTrader  # noqa: E402
from app.services.price_monitor import PriceMonitor  # noqa: E402

# ── Active Bot Tracking ───────────────────────────────────────────
# The "active bot" is the bot whose data the frontend sees via the
# standard /api/portfolio, /api/orders, /api/triggers endpoints.
# It auto-switches when the user saves a new LLM config.

_active_bot_id: str = "default"


def _get_active_bot_id() -> str:
    return _active_bot_id


def _set_active_bot(bot_id: str) -> None:
    global _active_bot_id
    _active_bot_id = bot_id
    logger.info("[ActiveBot] Switched to bot_id=%s", bot_id)

    # Sync global LLM settings to match this bot's stored config
    # so the next run_full_loop uses the correct model.
    from app.services.bot_registry import BotRegistry as _BReg

    bot = _BReg.get_bot(bot_id)
    if bot:
        settings.LLM_MODEL = bot.get("model_name", settings.LLM_MODEL)
        settings.LLM_CONTEXT_SIZE = bot.get("context_length", settings.LLM_CONTEXT_SIZE)
        settings.LLM_TEMPERATURE = bot.get("temperature", settings.LLM_TEMPERATURE)
        settings.LLM_TOP_P = bot.get("top_p", settings.LLM_TOP_P)
        provider_url = bot.get("provider_url", "")
        if provider_url:
            settings.OLLAMA_URL = provider_url
        logger.info(
            "[ActiveBot] Synced settings: model=%s ctx=%d url=%s",
            settings.LLM_MODEL, settings.LLM_CONTEXT_SIZE, settings.OLLAMA_URL,
        )


def _active_trader() -> PaperTrader:
    """Return a PaperTrader scoped to the active bot."""
    return PaperTrader(bot_id=_active_bot_id)


_paper_trader = PaperTrader()  # Default trader for price monitor
_price_monitor = PriceMonitor(_paper_trader)


@app.get("/api/active-bot")
async def get_active_bot() -> dict:
    """Which bot is currently selected in the frontend."""
    from app.services.bot_registry import BotRegistry as _BReg

    bot = _BReg.get_bot(_active_bot_id)
    return {
        "bot_id": _active_bot_id,
        "bot": bot,
        "model_name": settings.LLM_MODEL,
    }


@app.put("/api/active-bot")
async def set_active_bot(req: dict) -> dict:
    """Switch the active bot. Body: {"bot_id": "..."}."""
    bid = req.get("bot_id", "default")
    _set_active_bot(bid)
    return {"status": "ok", "active_bot_id": bid, "synced_model": settings.LLM_MODEL}


@app.get("/api/portfolio")
async def get_portfolio() -> dict:
    """Current cash + positions + total value (active bot)."""
    return _active_trader().get_portfolio()


@app.get("/api/portfolio/history")
async def get_portfolio_history(
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict:
    """Portfolio snapshots over time (equity curve data)."""
    snapshots = _active_trader().get_portfolio_history(limit=limit)
    return {"count": len(snapshots), "snapshots": snapshots}


@app.get("/api/positions")
async def get_positions() -> dict:
    """All open positions with entry details (active bot)."""
    positions = _active_trader().get_positions()
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

    trader = _active_trader()
    positions = trader.get_positions()
    qty = 0
    for p in positions:
        if p["ticker"] == ticker:
            qty = p["qty"]
            break

    if qty <= 0:
        raise HTTPException(status_code=404, detail=f"No open position for {ticker}")

    order = trader.sell(
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
    """Order history (active bot)."""
    orders = _active_trader().get_orders(limit=limit)
    return {"count": len(orders), "orders": orders}


@app.get("/api/triggers")
async def get_triggers() -> dict:
    """Active price triggers (active bot)."""
    triggers = _active_trader().get_triggers()
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
    result = _active_trader().reset_portfolio(new_balance=balance)
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
        "sec_13f_count": result.sec_13f_count,
        "congress_count": result.congress_count,
        "rss_news_count": result.rss_news_count,
        "transcript_count": result.transcript_count,
        "duration_seconds": round(result.duration_seconds, 1),
    }


@app.get("/api/discovery/results")
async def get_discovery_results(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    """Get latest scored tickers from the aggregated table (paginated)."""
    offset = (page - 1) * page_size
    result = _discovery.get_latest_scores(limit=page_size, offset=offset)
    total = result["total"]
    pages = max(1, -(-total // page_size))  # ceil division
    return {
        "scores": result["scores"],
        "total": total,
        "page": page,
        "pages": pages,
        "page_size": page_size,
    }


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
               detail, metadata, loop_id, status,
               bot_id, model_name
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
            "bot_id": r[9] if len(r) > 9 else "default",
            "model_name": r[10] if len(r) > 10 else "",
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
    """Trigger the full autonomous loop using the active bot."""
    global _loop, _loop_task

    if _loop._state["running"]:
        raise HTTPException(status_code=409, detail="Loop is already running")

    bot_id = _get_active_bot_id()
    # Re-create the loop scoped to the active bot
    _loop = AutonomousLoop(
        max_tickers=max_tickers,
        bot_id=bot_id,
        model_name=settings.LLM_MODEL,
    )

    async def _run() -> None:
        try:
            await _loop.run_full_loop()
            # Update bot stats after loop completes
            from app.services.bot_registry import BotRegistry as _BReg

            try:
                _BReg.update_stats(bot_id)
                _BReg.record_run(bot_id)
            except Exception:
                pass
        except Exception:
            logger.exception("[AutoLoop] Unhandled error in background loop")

    _loop_task = asyncio.create_task(_run())
    return {
        "status": "started",
        "bot_id": bot_id,
        "message": f"Full loop running (max_tickers={max_tickers}, bot={bot_id})",
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


# ── Stop / Emergency Stop / Shutdown ──────────────────────────────────


async def _unload_ollama_model() -> None:
    """Tell Ollama to unload the current model from VRAM."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{settings.OLLAMA_URL.rstrip('/')}/api/generate",
                json={"model": settings.LLM_MODEL, "keep_alive": "0"},
            )
            logger.info(
                "[Shutdown] Unloaded Ollama model: %s", settings.LLM_MODEL,
            )
    except Exception as exc:
        logger.warning("[Shutdown] Failed to unload Ollama model: %s", exc)


async def _do_emergency_stop() -> dict:
    """Core logic shared by the emergency-stop endpoint and shutdown handler."""
    global _loop, _loop_task

    from app.services.llm_service import (
        close_shared_client,
        request_shutdown,
    )

    # 1. Signal all LLM operations to stop
    request_shutdown()

    # 2. Cancel the single-bot loop
    if _loop._state.get("running"):
        _loop.cancel()
    if _loop_task and not _loop_task.done():
        _loop_task.cancel()
        logger.info("[EmergencyStop] Cancelled single-bot loop task")

    # 3. Cancel the run-all loop
    global _run_all_task
    if _run_all_state.get("running"):
        _run_all_state["running"] = False
        _run_all_state["current_phase"] = "stopped"
    if _run_all_task and not _run_all_task.done():
        _run_all_task.cancel()
        logger.info("[EmergencyStop] Cancelled run-all task")

    # 4. Close the shared HTTP client (aborts in-flight requests)
    await close_shared_client()

    # 5. Unload the Ollama model from VRAM
    await _unload_ollama_model()

    # 6. Re-enable LLM operations for future use
    from app.services.llm_service import cancel_shutdown
    cancel_shutdown()

    logger.info("[EmergencyStop] All operations stopped")
    return {"status": "stopped", "message": "All operations cancelled, model unloaded"}


@app.post("/api/bot/stop-loop")
async def stop_loop() -> dict:
    """Stop the current bot loop gracefully."""
    global _loop, _loop_task

    if not _loop._state.get("running"):
        return {"status": "not_running"}

    _loop.cancel()
    if _loop_task and not _loop_task.done():
        _loop_task.cancel()

    # Unload the model to free VRAM
    await _unload_ollama_model()

    return {"status": "stopped", "message": "Loop cancelled, model unloaded"}


@app.post("/api/bot/emergency-stop")
async def emergency_stop() -> dict:
    """Nuclear stop — cancel ALL bot operations and unload the Ollama model.

    Stops: single-bot loop, run-all, scheduler jobs, in-flight LLM requests.
    """
    return await _do_emergency_stop()


@app.on_event("shutdown")
async def _on_server_shutdown() -> None:
    """Called when the server is killed (Ctrl+C, SIGTERM, etc).

    Ensures Ollama stops processing by unloading the model and closing
    all HTTP connections.
    """
    logger.info("[Shutdown] Server shutting down — stopping all operations")
    try:
        await _do_emergency_stop()
    except Exception as exc:
        logger.warning("[Shutdown] Cleanup error: %s", exc)
    logger.info("[Shutdown] Cleanup complete")

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

    # Auto-select the first registered bot so the Portfolio tab
    # doesn't show orphaned data from the phantom "default" bot.
    try:
        registered = BotRegistry.list_bots()
        if registered:
            first_bot_id = registered[0]["bot_id"]
            _set_active_bot(first_bot_id)
            logger.info(
                "[Boot] Active bot auto-set to first registered bot: %s",
                first_bot_id,
            )
    except Exception:
        logger.warning("[Boot] Could not auto-select active bot")

    # ── Auto-pull embedding model for RAG ──────────────────────
    # Ensures the embedding model (e.g. nomic-embed-text) is available
    # in Ollama before any trading cycle tries to use it.
    if getattr(settings, "RAG_ENABLED", True):
        try:
            from app.services.embedding_service import EmbeddingService

            embed_svc = EmbeddingService()
            model_ok = await embed_svc.ensure_model_loaded()
            if model_ok:
                logger.info(
                    "[Boot] ✅ Embedding model ready: %s",
                    embed_svc.model,
                )
            else:
                logger.warning(
                    "[Boot] ⚠️ Embedding model %s not available — "
                    "RAG will be degraded until model is pulled",
                    embed_svc.model,
                )
        except Exception as exc:
            logger.warning(
                "[Boot] Could not pre-pull embedding model: %s", exc,
            )


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


# ══════════════════════════════════════════════════════════════════════
# MULTI-BOT LEADERBOARD API
# ══════════════════════════════════════════════════════════════════════

from app.services.bot_registry import BotRegistry  # noqa: E402


class BotCreateRequest(BaseModel):
    """Request body for creating a new bot."""

    model_name: str
    display_name: str = ""
    provider: str = "ollama"
    provider_url: str = "http://localhost:11434"
    context_length: int = 8192
    temperature: float = 0.3
    top_p: float = 1.0
    max_tokens: int = 0
    eval_batch_size: int = 512
    flash_attention: bool = True
    num_experts: int = 0
    gpu_offload: bool = True


class BotSettingsUpdate(BaseModel):
    """Request body for updating bot settings."""

    display_name: str | None = None
    provider: str | None = None
    provider_url: str | None = None
    context_length: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    eval_batch_size: int | None = None
    flash_attention: bool | None = None
    num_experts: int | None = None
    gpu_offload: bool | None = None


class ModelLoadRequest(BaseModel):
    """Request body for loading a model via LM Studio API."""

    model: str
    base_url: str = "http://localhost:1234"
    context_length: int = 0
    eval_batch_size: int = 0
    flash_attention: bool = True
    num_experts: int = 0
    gpu_offload: bool = True


@app.get("/api/bots")
async def list_bots(
    include_inactive: bool = Query(default=False),
) -> dict:
    """List all registered bots."""
    bots = BotRegistry.list_bots(include_inactive=include_inactive)
    return {"count": len(bots), "bots": bots}


@app.post("/api/bots")
async def create_bot(req: BotCreateRequest) -> dict:
    """Register a new bot with its LLM settings."""
    bot = BotRegistry.register_bot(
        model_name=req.model_name,
        display_name=req.display_name,
        provider=req.provider,
        provider_url=req.provider_url,
        context_length=req.context_length,
        temperature=req.temperature,
        top_p=req.top_p,
        max_tokens=req.max_tokens,
        eval_batch_size=req.eval_batch_size,
        flash_attention=req.flash_attention,
        num_experts=req.num_experts,
        gpu_offload=req.gpu_offload,
    )
    return {"status": "created", "bot": bot}


@app.get("/api/bots/{bot_id}")
async def get_bot(bot_id: str) -> dict:
    """Get a single bot with its config and stats."""
    bot = BotRegistry.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} not found")
    return bot


@app.delete("/api/bots/{bot_id}")
async def delete_bot_endpoint(bot_id: str, hard: bool = False) -> dict:
    """Delete a bot from the leaderboard.

    ?hard=true  → permanently delete bot + all related data
    ?hard=false → soft delete (set status='deleted')
    """
    deleted = BotRegistry.delete_bot(bot_id, hard=hard)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} not found")
    return {
        "status": "deleted",
        "bot_id": bot_id,
        "hard": hard,
    }


@app.put("/api/bots/{bot_id}/settings")
async def update_bot_settings(bot_id: str, req: BotSettingsUpdate) -> dict:
    """Update LLM settings for a specific bot."""
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="No settings to update")
    bot = BotRegistry.update_bot_settings(bot_id, data)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} not found")
    return {"status": "updated", "bot": bot}



class BotReorderRequest(BaseModel):
    """Request body for reordering bots."""

    order: list[str]  # list of bot_ids in desired order


@app.put("/api/bots/reorder")
async def reorder_bots(req: BotReorderRequest) -> dict:
    """Set the queue order for Run All Bots."""
    BotRegistry.reorder_bots(req.order)
    return {"status": "reordered", "count": len(req.order)}


@app.get("/api/leaderboard")
async def get_leaderboard() -> dict:
    """Get all bots ranked by total P&L."""
    from app.config import settings as _cfg

    # Recalculate stats for all active bots
    bots = BotRegistry.list_bots()
    for bot in bots:
        try:
            BotRegistry.update_stats(bot["bot_id"])
        except Exception:
            pass  # Skip bots with no data yet

    rankings = BotRegistry.get_leaderboard()

    # Inject VRAM stats for UI tooltip
    for bot in rankings:
        model = bot.get("model_name", "")
        bot["computed_max_ctx"] = bot.get("context_length")
        bot["computed_vram_gb"] = None

    return {"count": len(rankings), "leaderboard": rankings}


# ═══════════════════════════════════════════════════════════════════
#  DASHBOARD   Aggregate analytics across all bots + institutional
# ═══════════════════════════════════════════════════════════════════


@app.get("/api/dashboard/summary")
async def dashboard_summary() -> dict:
    """Aggregate stats across all bots for the dashboard header."""
    db = get_db()

    # All active bots
    bots = db.execute(
        "SELECT bot_id, display_name, total_pnl, total_trades, "
        "win_rate, max_drawdown FROM bots WHERE status = 'active'"
    ).fetchall()

    total_portfolio = 0.0
    total_pnl = 0.0
    total_trades = 0
    combined_wins = 0
    combined_sells = 0
    best_bot = {"name": "—", "pnl": 0.0}
    worst_bot = {"name": "—", "pnl": 0.0}

    for bot_id, name, pnl, trades, wr, dd in bots:
        # Latest snapshot for portfolio value
        snap = db.execute(
            "SELECT total_portfolio_value FROM portfolio_snapshots "
            "WHERE bot_id = ? ORDER BY timestamp DESC LIMIT 1",
            [bot_id],
        ).fetchone()
        val = snap[0] if snap and snap[0] else 0.0
        total_portfolio += val
        total_pnl += pnl or 0
        total_trades += trades or 0

        # Win/loss counts
        sells = db.execute(
            "SELECT COUNT(*) FROM orders "
            "WHERE bot_id = ? AND side = 'sell'",
            [bot_id],
        ).fetchone()[0]
        wins = db.execute(
            "SELECT COUNT(*) FROM orders "
            "WHERE bot_id = ? AND side = 'sell' "
            "AND CAST(json_extract_string("
            "CASE WHEN signal LIKE '{%' THEN signal ELSE '{}' END, "
            "'$.realized_pnl') AS DOUBLE) > 0",
            [bot_id],
        ).fetchone()[0]
        combined_wins += wins
        combined_sells += sells

        if (pnl or 0) > best_bot["pnl"]:
            best_bot = {"name": name, "pnl": round(pnl or 0, 2)}
        if (pnl or 0) < worst_bot["pnl"]:
            worst_bot = {"name": name, "pnl": round(pnl or 0, 2)}

    overall_wr = (
        round(combined_wins / combined_sells * 100, 1)
        if combined_sells > 0
        else 0.0
    )

    # Top gaining and losing positions across all bots
    top_gain = db.execute(
        "SELECT p.ticker, p.bot_id, b.display_name, p.qty, "
        "p.avg_entry_price "
        "FROM positions p JOIN bots b ON p.bot_id = b.bot_id "
        "WHERE b.status = 'active' "
        "ORDER BY p.qty * p.avg_entry_price DESC LIMIT 1"
    ).fetchone()

    return {
        "total_portfolio_value": round(total_portfolio, 2),
        "total_pnl": round(total_pnl, 2),
        "total_trades": total_trades,
        "overall_win_rate": overall_wr,
        "bot_count": len(bots),
        "best_bot": best_bot,
        "worst_bot": worst_bot,
        "top_position": {
            "ticker": top_gain[0],
            "bot": top_gain[2],
            "qty": top_gain[3],
            "avg_price": round(top_gain[4], 2),
        }
        if top_gain
        else None,
    }


@app.get("/api/dashboard/bots/compare")
async def dashboard_bots_compare() -> dict:
    """Per-bot comparison data: equity curves, trades, positions."""
    db = get_db()
    bots = db.execute(
        "SELECT bot_id, display_name, model_name, total_pnl, "
        "total_trades, win_rate, max_drawdown, last_run_at, "
        "created_at FROM bots WHERE status = 'active' "
        "ORDER BY total_pnl DESC"
    ).fetchall()

    result = []
    for (
        bot_id, name, model, pnl, trades, wr, dd,
        last_run, created,
    ) in bots:
        # Equity curve (last 30 snapshots)
        snaps = db.execute(
            "SELECT timestamp, total_portfolio_value, cash_balance, "
            "realized_pnl, unrealized_pnl "
            "FROM portfolio_snapshots WHERE bot_id = ? "
            "ORDER BY timestamp DESC LIMIT 30",
            [bot_id],
        ).fetchall()
        equity = [
            {
                "ts": str(s[0]),
                "value": round(s[1] or 0, 2),
                "cash": round(s[2] or 0, 2),
                "realized": round(s[3] or 0, 2),
                "unrealized": round(s[4] or 0, 2),
            }
            for s in reversed(snaps)
        ]

        # Starting balance
        first = db.execute(
            "SELECT total_portfolio_value FROM portfolio_snapshots "
            "WHERE bot_id = ? ORDER BY timestamp ASC LIMIT 1",
            [bot_id],
        ).fetchone()
        starting = first[0] if first and first[0] else 0.0
        current = equity[-1]["value"] if equity else 0.0
        ret_pct = (
            round((current - starting) / starting * 100, 2)
            if starting > 0
            else 0.0
        )

        # Current positions
        positions = db.execute(
            "SELECT ticker, qty, avg_entry_price FROM positions "
            "WHERE bot_id = ?",
            [bot_id],
        ).fetchall()

        # Recent trades (last 10)
        recent = db.execute(
            "SELECT id, ticker, side, qty, price, filled_at, signal "
            "FROM orders WHERE bot_id = ? "
            "ORDER BY created_at DESC LIMIT 10",
            [bot_id],
        ).fetchall()

        result.append({
            "bot_id": bot_id,
            "display_name": name,
            "model_name": model,
            "total_pnl": round(pnl or 0, 2),
            "total_trades": trades or 0,
            "win_rate": round(wr or 0, 1),
            "max_drawdown": round(dd or 0, 4),
            "return_pct": ret_pct,
            "starting_balance": round(starting, 2),
            "current_value": round(current, 2),
            "last_run_at": str(last_run) if last_run else None,
            "created_at": str(created) if created else None,
            "equity_curve": equity,
            "positions": [
                {
                    "ticker": p[0],
                    "qty": p[1],
                    "avg_price": round(p[2], 2),
                }
                for p in positions
            ],
            "recent_trades": [
                {
                    "id": t[0],
                    "ticker": t[1],
                    "side": t[2],
                    "qty": t[3],
                    "price": round(t[4], 2),
                    "filled_at": str(t[5]) if t[5] else None,
                    "signal": t[6],
                }
                for t in recent
            ],
        })

    return {"bots": result, "count": len(result)}


@app.get("/api/dashboard/activity")
async def dashboard_activity(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """Unified trade log across ALL bots, most recent first."""
    db = get_db()
    rows = db.execute(
        "SELECT o.id, o.ticker, o.side, o.qty, o.price, "
        "o.filled_at, o.created_at, o.signal, o.bot_id, "
        "b.display_name "
        "FROM orders o "
        "LEFT JOIN bots b ON o.bot_id = b.bot_id "
        "ORDER BY o.created_at DESC "
        f"LIMIT {limit}"
    ).fetchall()

    trades = []
    for r in rows:
        # Try to extract realized_pnl from signal JSON
        pnl = None
        sig = r[7] or ""
        if sig.startswith("{"):
            try:
                import json as _json
                sig_data = _json.loads(sig)
                pnl = sig_data.get("realized_pnl")
            except Exception:
                pass

        trades.append({
            "id": r[0],
            "ticker": r[1],
            "side": r[2],
            "qty": r[3],
            "price": round(r[4], 2),
            "filled_at": str(r[5]) if r[5] else None,
            "created_at": str(r[6]) if r[6] else None,
            "realized_pnl": round(pnl, 2) if pnl else None,
            "bot_id": r[8],
            "bot_name": r[9] or r[8],
        })

    return {"trades": trades, "count": len(trades)}


@app.get("/api/dashboard/institutional/movers")
async def dashboard_institutional_movers() -> dict:
    """Top institutional buys and sells from 13F data this quarter."""
    db = get_db()

    # Get the two most recent quarters
    quarters = db.execute(
        "SELECT DISTINCT filing_quarter FROM sec_13f_holdings "
        "ORDER BY filing_quarter DESC LIMIT 2"
    ).fetchall()

    if len(quarters) < 2:
        return {"buys": [], "sells": [], "quarter": None}

    current_q = quarters[0][0]
    prior_q = quarters[1][0]

    # Current quarter holdings
    current = db.execute(
        "SELECT h.ticker, h.cik, f.filer_name, h.shares, h.value_usd "
        "FROM sec_13f_holdings h "
        "JOIN sec_13f_filers f ON h.cik = f.cik "
        "WHERE h.filing_quarter = ?",
        [current_q],
    ).fetchall()

    # Prior quarter holdings map: (cik, ticker) -> shares
    prior = db.execute(
        "SELECT cik, ticker, shares FROM sec_13f_holdings "
        "WHERE filing_quarter = ?",
        [prior_q],
    ).fetchall()
    prior_map = {(r[0], r[1]): r[2] for r in prior}

    movers = []
    for ticker, cik, fund_name, shares, value in current:
        prev_shares = prior_map.get((cik, ticker), 0)
        delta = (shares or 0) - (prev_shares or 0)
        if delta == 0:
            continue
        movers.append({
            "ticker": ticker,
            "fund": fund_name,
            "shares": shares,
            "prev_shares": prev_shares,
            "delta": delta,
            "delta_pct": (
                round(delta / prev_shares * 100, 1)
                if prev_shares and prev_shares > 0
                else None
            ),
            "value_usd": value,
            "direction": "BUY" if delta > 0 else "SELL",
        })

    # Also detect sell-outs (in prior but not in current)
    current_keys = {(r[1], r[0]) for r in current}  # (cik, ticker)
    for (cik, ticker), prev_shares in prior_map.items():
        if (cik, ticker) not in current_keys and prev_shares > 0:
            fund_row = db.execute(
                "SELECT filer_name FROM sec_13f_filers WHERE cik = ?",
                [cik],
            ).fetchone()
            movers.append({
                "ticker": ticker,
                "fund": fund_row[0] if fund_row else cik,
                "shares": 0,
                "prev_shares": prev_shares,
                "delta": -prev_shares,
                "delta_pct": -100.0,
                "value_usd": 0,
                "direction": "SELL",
            })

    # Sort: biggest buys and biggest sells
    buys = sorted(
        [m for m in movers if m["direction"] == "BUY"],
        key=lambda m: m["delta"],
        reverse=True,
    )[:20]
    sells = sorted(
        [m for m in movers if m["direction"] == "SELL"],
        key=lambda m: m["delta"],
    )[:20]

    return {
        "buys": buys,
        "sells": sells,
        "quarter": current_q,
        "prior_quarter": prior_q,
        "total_movers": len(movers),
    }


@app.get("/api/bots/{bot_id}/portfolio")
async def get_bot_portfolio(bot_id: str) -> dict:
    """Get portfolio for a specific bot."""
    bot = BotRegistry.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} not found")
    trader = PaperTrader(bot_id=bot_id)
    return trader.get_portfolio()


@app.get("/api/bots/{bot_id}/orders")
async def get_bot_orders(
    bot_id: str,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """Get order history for a specific bot."""
    bot = BotRegistry.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} not found")
    trader = PaperTrader(bot_id=bot_id)
    orders = trader.get_orders(limit=limit)
    return {"count": len(orders), "orders": orders}


@app.get("/api/bots/{bot_id}/watchlist")
async def get_bot_watchlist(bot_id: str) -> dict:
    """Get watchlist for a specific bot."""
    from app.services.watchlist_manager import WatchlistManager

    bot = BotRegistry.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} not found")
    wm = WatchlistManager(bot_id=bot_id)
    watchlist = wm.get_watchlist()
    return {"count": len(watchlist), "watchlist": watchlist}


@app.post("/api/bots/{bot_id}/run")
async def run_bot_loop(bot_id: str, max_tickers: int = 10) -> dict:
    """Trigger a full trading loop for a specific bot."""
    bot = BotRegistry.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} not found")

    loop = AutonomousLoop(
        max_tickers=max_tickers,
        bot_id=bot_id,
        model_name=bot.get("model_name", ""),
    )
    BotRegistry.record_run(bot_id)

    async def _run() -> None:
        try:
            await loop.run_full_loop()
            BotRegistry.update_stats(bot_id)
        except Exception:
            logger.exception("[BotAPI] Loop error for bot %s", bot_id)

    asyncio.create_task(_run())
    return {
        "status": "started",
        "bot_id": bot_id,
        "message": f"Full loop running for bot {bot['display_name']}",
    }


# ── Run-All-Bots Sequential Execution ─────────────────────────────

_MAX_RUN_ALL_LOG = 500  # Cap in-memory log entries to prevent unbounded growth

_run_all_state: dict = {
    "running": False,
    "total_bots": 0,
    "completed": 0,
    "current_bot": None,
    "current_phase": None,
    "results": [],
    "log": [],
    "started_at": None,
}
_run_all_task: asyncio.Task | None = None


def _run_all_log(
    message: str,
    *,
    level: str = "info",
    phase: str = "system",
    bot_id: str = "",
    bot_name: str = "",
) -> None:
    """Append a structured log entry to the run-all state."""
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "level": level,
        "phase": phase,
        "bot_id": bot_id,
        "bot_name": bot_name,
        "message": message,
    }
    _run_all_state["log"].append(entry)
    # Keep log bounded
    if len(_run_all_state["log"]) > _MAX_RUN_ALL_LOG:
        _run_all_state["log"] = _run_all_state["log"][-_MAX_RUN_ALL_LOG:]


@app.post("/api/bots/run-all")
async def run_all_bots(max_tickers: int = Query(default=10)) -> dict:
    """Run the full loop for ALL active bots sequentially.

    For each bot: hot-patches global settings with the bot's LLM config,
    loads the model via LM Studio API, runs the autonomous loop, then
    moves to the next bot.
    """
    global _run_all_task

    if _run_all_state["running"]:
        raise HTTPException(
            status_code=409,
            detail="Run-all is already in progress",
        )

    bots = BotRegistry.list_bots()
    if not bots:
        raise HTTPException(status_code=400, detail="No active bots to run")

    # Reset state
    _run_all_state.update(
        {
            "running": True,
            "total_bots": len(bots),
            "completed": 0,
            "current_bot": None,
            "current_phase": None,
            "results": [],
            "log": [],
            "started_at": datetime.now().isoformat(),
        }
    )

    bot_names = ", ".join(
        b.get("display_name", b.get("model_name", "?")) for b in bots
    )
    _run_all_log(
        f"Starting sequential run for {len(bots)} bots: {bot_names}",
        level="system",
        phase="system",
    )

    async def _run_all() -> None:
        from app.services.llm_service import LLMService

        # ── Snapshot the user's config so we can restore after ──
        # The loop hot-patches settings.LLM_MODEL per bot, but
        # the user's chosen model should be restored when done.
        _saved_model = settings.LLM_MODEL
        _saved_ctx = settings.LLM_CONTEXT_SIZE
        _saved_temp = settings.LLM_TEMPERATURE
        _saved_top_p = settings.LLM_TOP_P
        _saved_url = settings.OLLAMA_URL
        _saved_bot_id = _get_active_bot_id()

        for idx, bot in enumerate(bots):
            bot_id = bot["bot_id"]
            model_name = bot.get("model_name", "")
            display_name = bot.get("display_name", model_name)
            _run_all_state["current_bot"] = {
                "bot_id": bot_id,
                "display_name": display_name,
                "model_name": model_name,
                "index": idx + 1,
            }
            _run_all_state["current_phase"] = "model_load"

            _run_all_log(
                f"▶ Starting bot {idx + 1}/{len(bots)}: {display_name}",
                level="info",
                phase="system",
                bot_id=bot_id,
                bot_name=display_name,
            )

            logger.info(
                "[RunAll] ▶ Bot %d/%d: %s (%s)",
                idx + 1,
                len(bots),
                display_name,
                model_name,
            )

            bot_result: dict = {
                "bot_id": bot_id,
                "display_name": display_name,
                "model_name": model_name,
                "status": "pending",
            }

            try:
                # ── 1. Hot-patch global settings with this bot's config ──
                settings.LLM_MODEL = model_name
                settings.LLM_CONTEXT_SIZE = bot.get("context_length", 8192)
                settings.LLM_TEMPERATURE = bot.get("temperature", 0.3)
                settings.LLM_TOP_P = bot.get("top_p", 1.0)

                # Sync active bot so Portfolio tab shows this bot's data
                _set_active_bot(bot_id)

                provider_url = bot.get("provider_url", "")
                if provider_url:
                    settings.OLLAMA_URL = provider_url

                _run_all_log(
                    f"Config applied: ollama / ctx={bot.get('context_length', 8192)} "
                    f"/ temp={bot.get('temperature', 0.3)}",
                    level="info",
                    phase="model_load",
                    bot_id=bot_id,
                    bot_name=display_name,
                )

                # ── 2. Unload previous model, then warm new one ──
                ollama_url = settings.OLLAMA_URL.rstrip("/")

                _run_all_log(
                    "Unloading all Ollama models to free VRAM…",
                    level="info",
                    phase="model_unload",
                    bot_id=bot_id,
                    bot_name=display_name,
                )
                try:
                    freed = await LLMService.unload_all_ollama_models(ollama_url)
                    if freed:
                        _run_all_log(
                            f"🧹 Unloaded {freed} Ollama model(s) from VRAM",
                            level="success",
                            phase="model_unload",
                            bot_id=bot_id,
                            bot_name=display_name,
                        )
                        logger.info(
                            "[RunAll] 🧹 Unloaded %d Ollama model(s) "
                            "before warming %s",
                            freed,
                            model_name,
                        )
                except Exception as exc:
                    _run_all_log(
                        f"⚠️ Ollama pre-unload failed: {exc}",
                        level="warn",
                        phase="model_unload",
                        bot_id=bot_id,
                        bot_name=display_name,
                    )
                    logger.warning(
                        "[RunAll] ⚠️ Ollama pre-unload failed, "
                        "attempting warm anyway",
                    )

                # Pre-warm the new model into VRAM
                _run_all_log(
                    f"Pre-warming Ollama model: {model_name}…",
                    level="info",
                    phase="model_load",
                    bot_id=bot_id,
                    bot_name=display_name,
                )
                _run_all_state["current_phase"] = "model_load"
                try:
                    warm = await LLMService.verify_and_warm_ollama_model(
                        ollama_url,
                        model_name,
                        keep_alive="2h",
                    )
                    if warm.get("status") == "oom_error":
                        sug = warm.get("suggested_ctx", 8192)
                        settings.LLM_CONTEXT_SIZE = sug
                        _run_all_log(
                            f"⚠️ OOM at ctx={warm.get('requested_ctx')} "
                            f"— using suggested ctx={sug}",
                            level="warn",
                            phase="model_load",
                            bot_id=bot_id,
                            bot_name=display_name,
                        )
                        logger.warning(
                            "[RunAll] OOM for %s — using ctx=%d",
                            model_name, sug,
                        )
                    elif warm.get("pre_warmed"):
                        rec_ctx = warm.get("recommended_ctx", "?")
                        vram_bytes = warm.get("vram_bytes", 0)
                        vram_gb = vram_bytes / (1024 ** 3) if vram_bytes else 0

                        _run_all_log(
                            f"✅ Ollama model warmed: {model_name} | "
                            f"VRAM Used: {vram_gb:.1f}GB | "
                            f"eff_ctx={rec_ctx}",
                            level="success",
                            phase="model_load",
                            bot_id=bot_id,
                            bot_name=display_name,
                        )
                        logger.info(
                            "[RunAll] ✅ Ollama model warmed: %s",
                            model_name,
                        )
                    else:
                        _run_all_log(
                            f"⚠️ Ollama warm returned: {warm.get('status')}",
                            level="warn",
                            phase="model_load",
                            bot_id=bot_id,
                            bot_name=display_name,
                        )
                        logger.warning(
                            "[RunAll] ⚠️ Ollama warm failed for %s: %s",
                            model_name,
                            warm.get("status"),
                        )
                except Exception as exc:
                    _run_all_log(
                        f"⚠️ Ollama warm failed: {exc} — attempting loop anyway",
                        level="warn",
                        phase="model_load",
                        bot_id=bot_id,
                        bot_name=display_name,
                    )
                    logger.exception(
                        "[RunAll] ⚠️ Ollama warm failed for %s, "
                        "attempting loop anyway",
                        model_name,
                    )

                # ── 2b. Smoke-test the model before running the loop ──
                _run_all_log(
                    "Verifying model can serve requests…",
                    level="info",
                    phase="model_load",
                    bot_id=bot_id,
                    bot_name=display_name,
                )
                try:
                    test_llm = LLMService()
                    await test_llm.chat(
                        system="You are a test. Reply with exactly: OK",
                        user="Say OK",
                        response_format="text",
                        max_tokens=10,
                    )
                    _run_all_log(
                        "✅ Model verification passed",
                        level="success",
                        phase="model_load",
                        bot_id=bot_id,
                        bot_name=display_name,
                    )
                except Exception as verify_exc:
                    _run_all_log(
                        f"❌ Model verification FAILED: {verify_exc} "
                        f"— skipping bot",
                        level="error",
                        phase="model_load",
                        bot_id=bot_id,
                        bot_name=display_name,
                    )
                    logger.error(
                        "[RunAll] ❌ Model verification failed for %s: %s",
                        model_name,
                        verify_exc,
                    )
                    bot_result["status"] = "skipped"
                    bot_result["error"] = (
                        f"Model verification failed: {verify_exc}"
                    )
                    _run_all_state["results"].append(bot_result)
                    _run_all_state["completed"] = idx + 1
                    continue

                # ── 3. Run the autonomous loop for this bot ──
                _run_all_log(
                    f"Starting autonomous loop (max_tickers={max_tickers})…",
                    level="info",
                    phase="discovery",
                    bot_id=bot_id,
                    bot_name=display_name,
                )
                _run_all_state["current_phase"] = "discovery"

                loop = AutonomousLoop(
                    max_tickers=max_tickers,
                    bot_id=bot_id,
                    model_name=model_name,
                )
                BotRegistry.record_run(bot_id)

                # Run the loop — periodically merge sub-logs
                import asyncio as _aio

                loop_future = _aio.ensure_future(loop.run_full_loop())
                last_sub_log_len = 0

                while not loop_future.done():
                    await _aio.sleep(2)
                    # Merge new sub-log entries from the AutonomousLoop
                    sub_log = loop._state.get("log", [])
                    if len(sub_log) > last_sub_log_len:
                        for entry in sub_log[last_sub_log_len:]:
                            msg = entry.get("message", "")
                            # Detect phase from message content
                            phase_guess = "system"
                            phase_val = loop._state.get("phase", "")
                            if phase_val:
                                phase_guess = phase_val
                            # Detect level from message
                            lvl = "info"
                            if "error" in msg.lower() or "failed" in msg.lower():
                                lvl = "error"
                            elif "⚠" in msg or "warning" in msg.lower():
                                lvl = "warn"
                            elif "✅" in msg or "complete" in msg.lower():
                                lvl = "success"
                            _run_all_log(
                                msg,
                                level=lvl,
                                phase=phase_guess,
                                bot_id=bot_id,
                                bot_name=display_name,
                            )
                        last_sub_log_len = len(sub_log)
                    # Update current phase
                    _run_all_state["current_phase"] = loop._state.get(
                        "phase", "running",
                    )

                # Await the result to propagate any exceptions
                await loop_future

                # Final sub-log merge
                sub_log = loop._state.get("log", [])
                if len(sub_log) > last_sub_log_len:
                    for entry in sub_log[last_sub_log_len:]:
                        msg = entry.get("message", "")
                        lvl = "info"
                        if "error" in msg.lower() or "failed" in msg.lower():
                            lvl = "error"
                        elif "⚠" in msg or "warning" in msg.lower():
                            lvl = "warn"
                        elif "✅" in msg or "complete" in msg.lower():
                            lvl = "success"
                        _run_all_log(
                            msg,
                            level=lvl,
                            phase=loop._state.get("phase", "done"),
                            bot_id=bot_id,
                            bot_name=display_name,
                        )

                # ── 4. Update stats after completion ──
                BotRegistry.update_stats(bot_id)
                bot_result["status"] = "done"
                _run_all_log(
                    f"✅ Bot {display_name} completed successfully",
                    level="success",
                    phase="system",
                    bot_id=bot_id,
                    bot_name=display_name,
                )
                logger.info(
                    "[RunAll] ✅ Bot %s completed successfully",
                    display_name,
                )

                # ── 5. Unload model after bot completes to free VRAM ──
                _run_all_state["current_phase"] = "model_unload"
                _run_all_log(
                    f"Unloading model {model_name} to free VRAM…",
                    level="info",
                    phase="model_unload",
                    bot_id=bot_id,
                    bot_name=display_name,
                )
                try:
                    await LLMService.unload_ollama_model(
                        settings.OLLAMA_URL.rstrip("/"),
                        model_name,
                    )
                    _run_all_log(
                        f"🧹 Model {model_name} unloaded",
                        level="success",
                        phase="model_unload",
                        bot_id=bot_id,
                        bot_name=display_name,
                    )
                    logger.info(
                        "[RunAll] 🧹 Model %s unloaded after completion",
                        model_name,
                    )
                except Exception as exc:
                    _run_all_log(
                        f"⚠️ Post-run unload failed: {exc}",
                        level="warn",
                        phase="model_unload",
                        bot_id=bot_id,
                        bot_name=display_name,
                    )
                    logger.warning(
                        "[RunAll] ⚠️ Post-run unload failed for %s",
                        model_name,
                    )

            except Exception as exc:
                bot_result["status"] = "error"
                bot_result["error"] = str(exc)
                _run_all_log(
                    f"❌ Bot {display_name} failed: {exc}",
                    level="error",
                    phase="system",
                    bot_id=bot_id,
                    bot_name=display_name,
                )
                logger.exception(
                    "[RunAll] ❌ Bot %s failed: %s",
                    display_name,
                    exc,
                )
                # Still try to unload on error to avoid VRAM leak
                try:
                    await LLMService.unload_ollama_model(
                        settings.OLLAMA_URL.rstrip("/"),
                        model_name,
                    )
                    _run_all_log(
                        "🧹 Emergency unload after error completed",
                        level="info",
                        phase="model_unload",
                        bot_id=bot_id,
                        bot_name=display_name,
                    )
                except Exception:
                    pass

            _run_all_state["results"].append(bot_result)
            _run_all_state["completed"] = idx + 1

        # All done — restore the user's original config
        settings.LLM_MODEL = _saved_model
        settings.LLM_CONTEXT_SIZE = _saved_ctx
        settings.LLM_TEMPERATURE = _saved_temp
        settings.LLM_TOP_P = _saved_top_p
        settings.OLLAMA_URL = _saved_url
        _set_active_bot(_saved_bot_id)
        # Persist restored config to disk
        settings.update_llm_config({
            "model": _saved_model,
            "context_size": _saved_ctx,
            "temperature": _saved_temp,
            "top_p": _saved_top_p,
            "ollama_url": _saved_url,
        })
        logger.info(
            "[RunAll] Restored user config: model=%s, "
            "active_bot=%s",
            _saved_model, _saved_bot_id,
        )

        _run_all_state["running"] = False
        _run_all_state["current_bot"] = None
        _run_all_state["current_phase"] = None
        _run_all_log(
            f"🏁 All {len(bots)} bots completed",
            level="success",
            phase="system",
        )
        logger.info(
            "[RunAll] 🏁 All %d bots completed",
            len(bots),
        )

    _run_all_task = asyncio.create_task(_run_all())
    return {
        "status": "started",
        "total_bots": len(bots),
        "message": f"Running all {len(bots)} bots sequentially",
    }


@app.get("/api/bots/run-all/status")
async def get_run_all_status() -> dict:
    """Poll progress of the run-all-bots operation.

    Returns full state including structured log entries for the live console.
    """
    return dict(_run_all_state)


# ── Cross-Bot Audit API ───────────────────────────────────────────────


@app.get("/api/audit/reports")
async def get_recent_audits(limit: int = Query(default=10)) -> list:
    """Get recent cross-bot audit reports."""
    from app.services.CrossBotAuditor import CrossBotAuditor
    return CrossBotAuditor.get_recent_audits(limit=limit)


@app.get("/api/audit/bot/{bot_id}")
async def get_bot_audits(bot_id: str, limit: int = Query(default=10)) -> dict:
    """Get audit reports for a specific bot."""
    from app.services.CrossBotAuditor import CrossBotAuditor
    audits = CrossBotAuditor.get_recent_audits(bot_id=bot_id, limit=limit)
    avg_score = CrossBotAuditor.get_bot_audit_score(bot_id)
    return {
        "bot_id": bot_id,
        "average_score": avg_score,
        "audits": audits,
    }


@app.post("/api/audit/toggle")
async def toggle_cross_audit() -> dict:
    """Toggle the cross-bot audit feature on/off."""
    current = settings.CROSS_AUDIT_ENABLED
    settings.CROSS_AUDIT_ENABLED = not current
    new_state = settings.CROSS_AUDIT_ENABLED
    logger.info("[Settings] Cross-bot audit toggled: %s", new_state)
    return {
        "cross_audit_enabled": new_state,
        "message": f"Cross-bot audit {'enabled' if new_state else 'disabled'}",
    }


@app.get("/api/diagnostics/pipeline-events")
async def get_pipeline_events(limit: int = Query(default=50)) -> list:
    """Get recent pipeline events (parse failures, repairs, tool usage)."""
    from app.database import get_db
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT bot_id, event_type, event_data, created_at "
            "FROM pipeline_events "
            "WHERE event_type LIKE 'trade_parse:%' OR event_type LIKE 'trading_agent:%' "
            "ORDER BY created_at DESC LIMIT ?",
            [limit],
        ).fetchall()
        cols = ["bot_id", "event_type", "event_data", "created_at"]
        events = []
        for r in rows:
            d = dict(zip(cols, r))
            d["created_at"] = str(d["created_at"]) if d["created_at"] else None
            try:
                d["event_data"] = json.loads(d["event_data"]) if d["event_data"] else {}
            except (json.JSONDecodeError, TypeError):
                pass
            events.append(d)
        return events
    except Exception:
        return []

@app.post("/api/bots/{bot_id}/reset")
async def reset_bot_portfolio(
    bot_id: str,
    req: PortfolioResetRequest | None = None,
) -> dict:
    """Reset portfolio for a specific bot."""
    bot = BotRegistry.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} not found")
    balance = req.balance if req and req.balance else None
    trader = PaperTrader(bot_id=bot_id)
    return trader.reset_portfolio(new_balance=balance)


# ── LM Studio Model Management ────────────────────────────────────


@app.post("/api/llm/load-model")
async def load_lm_model(req: ModelLoadRequest) -> dict:
    """Load a model via the LM Studio v1 API.

    Returns the actual configuration applied by LM Studio.
    """
    from app.services.llm_service import LLMService

    config = {
        "context_length": req.context_length,
        "eval_batch_size": req.eval_batch_size,
        "flash_attention": req.flash_attention,
        "num_experts": req.num_experts,
        "offload_kv_cache_to_gpu": req.gpu_offload,
    }
    try:
        result = await LLMService.load_model_with_config(
            base_url=req.base_url,
            model=req.model,
            config=config,
        )
        return {"status": "loaded", "result": result}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/api/llm/model-info")
async def get_loaded_model_info(
    base_url: str = Query(default="http://localhost:1234"),
) -> dict:
    """Get currently loaded model details from LM Studio."""
    from app.services.llm_service import LLMService

    models = await LLMService.get_loaded_model_info(base_url)
    return {"count": len(models), "models": models}


# ══════════════════════════════════════════════════════════════════════
# TRADE DECISION HISTORY (Phase 4 — Decision Audit Trail)
# ══════════════════════════════════════════════════════════════════════


@app.get("/api/decisions/{bot_id}")
async def get_trade_decisions(
    bot_id: str,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """Return recent trade decisions for a bot."""
    from app.services.decision_logger import DecisionLogger

    decisions = DecisionLogger.get_decisions(bot_id, limit=limit)
    return {"bot_id": bot_id, "decisions": decisions, "count": len(decisions)}


@app.get("/api/decisions/detail/{decision_id}")
async def get_decision_detail(decision_id: str) -> dict:
    """Return a single decision + its execution details."""
    from app.services.decision_logger import DecisionLogger

    result = DecisionLogger.get_decision_with_execution(decision_id)
    if not result:
        raise HTTPException(status_code=404, detail="Decision not found")
    return result


# ══════════════════════════════════════════════════════════════════════
# LLM AUDIT LOG (Thought Ledger)
# ══════════════════════════════════════════════════════════════════════


@app.get("/api/audit/{ticker}")
async def get_audit_logs_for_ticker(
    ticker: str,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """Return LLM audit logs for a specific ticker."""
    from app.services.llm_audit_logger import LLMAuditLogger

    logs = LLMAuditLogger.get_logs_for_ticker(ticker.upper(), limit=limit)
    return {"ticker": ticker.upper(), "logs": logs, "count": len(logs)}


@app.get("/api/audit/recent")
async def get_recent_audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """Return most recent LLM audit logs across all tickers."""
    from app.services.llm_audit_logger import LLMAuditLogger

    logs = LLMAuditLogger.get_recent_logs(limit=limit)
    return {"logs": logs, "count": len(logs)}


@app.get("/api/audit/cycle/{cycle_id}")
async def get_audit_logs_for_cycle(cycle_id: str) -> dict:
    """Return all LLM audit logs for a specific trading cycle."""
    from app.services.llm_audit_logger import LLMAuditLogger

    logs = LLMAuditLogger.get_logs_for_cycle(cycle_id)
    return {"cycle_id": cycle_id, "logs": logs, "count": len(logs)}


# ══════════════════════════════════════════════════════════════════════
# TRACKER API — Enhanced accordion views for 13F + Congress data
# ══════════════════════════════════════════════════════════════════════


@app.get("/api/tracker/funds")
async def tracker_fund_list(
    sort: str = Query(default="value", description="Sort by: value | holdings | name"),
    search: str = Query(default=""),
) -> dict:
    """Fund summary cards — name, CIK, latest quarter, totals."""
    db = get_db()
    rows = db.execute(
        """
        SELECT f.cik, f.filer_name, f.latest_quarter, f.is_active,
               COUNT(h.ticker) AS holding_count,
               COALESCE(SUM(h.value_usd), 0) AS total_value
        FROM sec_13f_filers f
        LEFT JOIN sec_13f_holdings h
            ON f.cik = h.cik
            AND h.filing_quarter = f.latest_quarter
        GROUP BY f.cik, f.filer_name, f.latest_quarter, f.is_active
        ORDER BY total_value DESC
        """,
    ).fetchall()

    funds = []
    for cik, name, quarter, active, count, value in rows:
        if search and search.lower() not in (name or "").lower():
            continue
        funds.append({
            "cik": cik,
            "name": name,
            "latest_quarter": quarter,
            "is_active": active,
            "holding_count": count,
            "total_value_usd": value,
        })

    # Sort
    if sort == "holdings":
        funds.sort(key=lambda f: f["holding_count"], reverse=True)
    elif sort == "name":
        funds.sort(key=lambda f: (f["name"] or "").lower())
    # default: value (already sorted)

    return {"funds": funds, "count": len(funds)}


@app.get("/api/tracker/funds/{cik}/holdings")
async def tracker_fund_holdings(
    cik: str,
    search: str = Query(default=""),
    sort: str = Query(default="value_usd"),
    order: str = Query(default="desc"),
) -> dict:
    """Holdings for one fund with QoQ change detection and % of portfolio."""
    db = get_db()

    # Get latest quarter for this filer
    filer = db.execute(
        "SELECT filer_name, latest_quarter FROM sec_13f_filers WHERE cik = ?",
        [cik],
    ).fetchone()
    if not filer:
        raise HTTPException(status_code=404, detail=f"Filer {cik} not found")

    filer_name, latest_q = filer

    # Determine prior quarter
    prior_q = _prior_quarter(latest_q) if latest_q else None

    # Current quarter holdings
    current = db.execute(
        """
        SELECT ticker, name_of_issuer, shares, value_usd, cusip
        FROM sec_13f_holdings
        WHERE cik = ? AND filing_quarter = ?
        ORDER BY value_usd DESC
        """,
        [cik, latest_q],
    ).fetchall()

    # Prior quarter holdings for QoQ comparison
    prior_map: dict[str, tuple] = {}
    if prior_q:
        prior_rows = db.execute(
            "SELECT ticker, shares, value_usd FROM sec_13f_holdings "
            "WHERE cik = ? AND filing_quarter = ?",
            [cik, prior_q],
        ).fetchall()
        for ticker, shares, value in prior_rows:
            prior_map[ticker] = (shares, value)

    # Total portfolio value for % calculation
    total_value = sum(r[3] or 0 for r in current) or 1  # avoid div by zero

    holdings = []
    for ticker, issuer, shares, value, cusip in current:
        if search and search.lower() not in (
            f"{ticker} {issuer}".lower()
        ):
            continue

        pct = round(((value or 0) / total_value) * 100, 2)

        # QoQ change detection
        if ticker in prior_map:
            prev_shares, prev_value = prior_map[ticker]
            if shares == prev_shares:
                qoq = "UNCHANGED"
            elif shares > prev_shares:
                qoq = "ADDED"
            else:
                qoq = "REDUCED"
            share_delta = (shares or 0) - (prev_shares or 0)
        else:
            qoq = "NEW"
            share_delta = shares or 0

        holdings.append({
            "ticker": ticker,
            "name_of_issuer": issuer,
            "shares": shares,
            "value_usd": value,
            "pct_of_portfolio": pct,
            "qoq_change": qoq,
            "share_change": share_delta,
            "cusip": cusip,
        })

    # Detect SOLD OUT — tickers in prior but not in current
    current_tickers = {r[0] for r in current}
    for ticker, (prev_shares, prev_value) in prior_map.items():
        if ticker not in current_tickers:
            if search and search.lower() not in ticker.lower():
                continue
            holdings.append({
                "ticker": ticker,
                "name_of_issuer": "",
                "shares": 0,
                "value_usd": 0,
                "pct_of_portfolio": 0,
                "qoq_change": "SOLD_OUT",
                "share_change": -(prev_shares or 0),
                "cusip": "",
            })

    # ── Multi-quarter trend calculation ──────────────────────────────
    # Pull ALL historical quarters for this filer to compute trends
    all_hist = db.execute(
        """
        SELECT ticker, filing_quarter, shares
        FROM sec_13f_holdings
        WHERE cik = ?
        ORDER BY filing_quarter ASC
        """,
        [cik],
    ).fetchall()

    # Build { ticker -> [(quarter, shares), ...] } ordered by quarter
    hist_map: dict[str, list[tuple[str, int]]] = {}
    for t, q, s in all_hist:
        hist_map.setdefault(t, []).append((q, s or 0))

    for h in holdings:
        ticker = h["ticker"]
        history = hist_map.get(ticker, [])

        if len(history) <= 1:
            h["trend_direction"] = "NEW"
            h["trend_streak"] = 0
            h["total_change_pct"] = 0
            continue

        # Calculate consecutive streak direction
        streak = 0
        direction = "STEADY"
        for i in range(len(history) - 1, 0, -1):
            delta = history[i][1] - history[i - 1][1]
            if i == len(history) - 1:
                # Set direction from latest change
                if delta > 0:
                    direction = "ACCUMULATING"
                elif delta < 0:
                    direction = "DUMPING"
                else:
                    direction = "STEADY"
                streak = 1
            else:
                # Extend streak if same direction
                if (direction == "ACCUMULATING" and delta > 0) or \
                   (direction == "DUMPING" and delta < 0):
                    streak += 1
                else:
                    break

        # Total change % from first to current
        first_shares = history[0][1]
        current_shares = history[-1][1]
        if first_shares > 0:
            total_pct = round(
                ((current_shares - first_shares) / first_shares)
                * 100, 1,
            )
        else:
            total_pct = 0

        h["trend_direction"] = direction
        h["trend_streak"] = streak
        h["total_change_pct"] = total_pct

    # Sort
    valid_sorts = (
        "ticker", "value_usd", "shares", "pct_of_portfolio",
        "trend_direction",
    )
    sort_key = sort if sort in valid_sorts else "value_usd"
    reverse = order.lower() != "asc"
    holdings.sort(
        key=lambda h: h.get(sort_key) or 0, reverse=reverse,
    )

    return {
        "cik": cik,
        "filer_name": filer_name,
        "quarter": latest_q,
        "prior_quarter": prior_q,
        "total_value_usd": total_value,
        "holdings": holdings,
        "count": len(holdings),
    }


@app.get("/api/tracker/funds/{cik}/history/{ticker}")
async def tracker_holding_history(cik: str, ticker: str) -> dict:
    """Position history for a specific ticker across all stored quarters."""
    db = get_db()
    rows = db.execute(
        """
        SELECT filing_quarter, filing_date, shares, value_usd
        FROM sec_13f_holdings
        WHERE cik = ? AND ticker = ?
        ORDER BY filing_quarter ASC
        """,
        [cik, ticker.upper()],
    ).fetchall()

    filer = db.execute(
        "SELECT filer_name FROM sec_13f_filers WHERE cik = ?", [cik],
    ).fetchone()

    history = []
    prev_shares = None
    for quarter, filing_date, shares, value in rows:
        change = None
        if prev_shares is not None:
            change = (shares or 0) - prev_shares
        history.append({
            "quarter": quarter,
            "filing_date": str(filing_date) if filing_date else None,
            "shares": shares,
            "value_usd": value,
            "share_change": change,
        })
        prev_shares = shares or 0

    return {
        "cik": cik,
        "filer_name": filer[0] if filer else cik,
        "ticker": ticker.upper(),
        "history": history,
        "quarters_held": len(history),
    }


@app.get("/api/tracker/funds/overlap")
async def tracker_fund_overlap(
    min_funds: int = Query(default=2, ge=2),
) -> dict:
    """Tickers held by multiple funds — institutional consensus signal."""
    db = get_db()
    rows = db.execute(
        """
        SELECT h.ticker, COUNT(DISTINCT h.cik) AS fund_count,
               SUM(h.value_usd) AS total_value,
               SUM(h.shares) AS total_shares,
               STRING_AGG(DISTINCT f.filer_name, ', ') AS fund_names
        FROM sec_13f_holdings h
        JOIN sec_13f_filers f ON h.cik = f.cik
        WHERE h.filing_quarter = f.latest_quarter
          AND h.ticker != '' AND h.ticker IS NOT NULL
        GROUP BY h.ticker
        HAVING COUNT(DISTINCT h.cik) >= ?
        ORDER BY fund_count DESC, total_value DESC
        LIMIT 100
        """,
        [min_funds],
    ).fetchall()

    overlap = []
    for ticker, fund_count, total_value, total_shares, fund_names in rows:
        overlap.append({
            "ticker": ticker,
            "fund_count": fund_count,
            "total_value_usd": total_value,
            "total_shares": total_shares,
            "fund_names": fund_names,
        })

    return {"overlap": overlap, "count": len(overlap), "min_funds": min_funds}


@app.get("/api/tracker/congress/members")
async def tracker_congress_members(
    sort: str = Query(default="trades"),
    search: str = Query(default=""),
) -> dict:
    """Congress member summary cards with trade stats."""
    db = get_db()
    rows = db.execute(
        """
        SELECT member_name, chamber,
               COUNT(*) AS trade_count,
               SUM(CASE WHEN tx_type LIKE '%Purchase%' THEN 1 ELSE 0 END) AS buys,
               SUM(CASE WHEN tx_type LIKE '%Sale%' THEN 1 ELSE 0 END) AS sells,
               MAX(tx_date) AS last_trade_date,
               MIN(tx_date) AS first_trade_date
        FROM congressional_trades
        WHERE member_name IS NOT NULL AND member_name != ''
        GROUP BY member_name, chamber
        ORDER BY trade_count DESC
        """,
    ).fetchall()

    members = []
    for name, chamber, count, buys, sells, last_trade, first_trade in rows:
        if search and search.lower() not in (name or "").lower():
            continue
        members.append({
            "member_name": name,
            "chamber": chamber,
            "trade_count": count,
            "buys": buys,
            "sells": sells,
            "last_trade_date": str(last_trade) if last_trade else None,
            "first_trade_date": str(first_trade) if first_trade else None,
        })

    if sort == "name":
        members.sort(key=lambda m: (m["member_name"] or "").lower())
    elif sort == "recent":
        members.sort(key=lambda m: m["last_trade_date"] or "", reverse=True)
    # default: trades (already sorted)

    return {"members": members, "count": len(members)}


@app.get("/api/tracker/congress/members/{name}/trades")
async def tracker_congress_member_trades(
    name: str,
    sort: str = Query(default="tx_date"),
    order: str = Query(default="desc"),
) -> dict:
    """Trades for a specific member with days-to-report calculation."""
    db = get_db()
    rows = db.execute(
        """
        SELECT id, ticker, asset_name, tx_type, tx_date,
               filed_date, amount_range, source_url
        FROM congressional_trades
        WHERE member_name = ?
        ORDER BY tx_date DESC
        """,
        [name],
    ).fetchall()

    trades = []
    for row_id, ticker, asset, tx_type, tx_date, filed_date, amount, url in rows:
        # Calculate days to report
        days_to_report = None
        if tx_date and filed_date:
            try:
                from datetime import date as date_cls
                td = (
                    tx_date if isinstance(tx_date, date_cls)
                    else date_cls.fromisoformat(str(tx_date))
                )
                fd = (
                    filed_date if isinstance(filed_date, date_cls)
                    else date_cls.fromisoformat(str(filed_date))
                )
                days_to_report = (fd - td).days
            except (ValueError, TypeError):
                pass

        trades.append({
            "id": row_id,
            "ticker": ticker,
            "asset_name": asset,
            "tx_type": tx_type,
            "tx_date": str(tx_date) if tx_date else None,
            "filed_date": str(filed_date) if filed_date else None,
            "days_to_report": days_to_report,
            "amount_range": amount,
            "source_url": url,
        })

    # Sort
    if sort == "ticker":
        trades.sort(key=lambda t: t.get("ticker") or "", reverse=(order == "desc"))
    elif sort == "days_to_report":
        trades.sort(key=lambda t: t.get("days_to_report") or 999, reverse=(order == "desc"))
    # default: tx_date (already sorted desc)

    return {"member_name": name, "trades": trades, "count": len(trades)}


@app.get("/api/tracker/tickers")
async def tracker_ticker_list(
    search: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """Searchable list of all tickers in 13F holdings."""
    db = get_db()
    if search:
        rows = db.execute(
            """
            SELECT DISTINCT ticker
            FROM sec_13f_holdings
            WHERE ticker IS NOT NULL AND ticker != ''
              AND ticker LIKE ?
            ORDER BY ticker ASC
            LIMIT ?
            """,
            [f"%{search.upper()}%", limit],
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT DISTINCT ticker
            FROM sec_13f_holdings
            WHERE ticker IS NOT NULL AND ticker != ''
            ORDER BY ticker ASC
            LIMIT ?
            """,
            [limit],
        ).fetchall()
    return {"tickers": [r[0] for r in rows], "count": len(rows)}


@app.get("/api/tracker/ticker/{ticker}/funds")
async def tracker_ticker_funds(ticker: str) -> dict:
    """All funds' position history for one ticker across quarters."""
    db = get_db()
    ticker = ticker.upper()

    # Get all holdings for this ticker, joined with filer name
    rows = db.execute(
        """
        SELECT h.cik, f.filer_name, h.filing_quarter,
               h.shares, h.value_usd, h.filing_date
        FROM sec_13f_holdings h
        JOIN sec_13f_filers f ON h.cik = f.cik
        WHERE h.ticker = ?
        ORDER BY h.filing_quarter ASC, f.filer_name ASC
        """,
        [ticker],
    ).fetchall()

    if not rows:
        return {
            "ticker": ticker,
            "funds": [],
            "quarters": [],
            "count": 0,
        }

    # Collect all unique quarters and funds
    all_quarters: list[str] = []
    fund_map: dict[str, dict] = {}  # cik -> fund data

    for cik, name, quarter, shares, value, filing_date in rows:
        if quarter not in all_quarters:
            all_quarters.append(quarter)

        if cik not in fund_map:
            fund_map[cik] = {
                "cik": cik,
                "name": name,
                "quarters": {},
                "first_seen": quarter,
                "last_seen": quarter,
            }

        fund_map[cik]["quarters"][quarter] = {
            "shares": shares,
            "value_usd": value,
            "filing_date": (
                str(filing_date) if filing_date else None
            ),
        }
        fund_map[cik]["last_seen"] = quarter

    # Build fund timeline with QoQ status per quarter
    funds = []
    for cik, data in fund_map.items():
        timeline = []
        prev_shares = None
        for q in all_quarters:
            entry = data["quarters"].get(q)
            if entry:
                shares = entry["shares"] or 0
                if prev_shares is None:
                    status = "NEW"
                elif shares > prev_shares:
                    status = "ADDED"
                elif shares < prev_shares:
                    status = "REDUCED"
                else:
                    status = "HELD"
                change = (
                    shares - prev_shares
                    if prev_shares is not None
                    else shares
                )
                timeline.append({
                    "quarter": q,
                    "shares": shares,
                    "value_usd": entry["value_usd"],
                    "status": status,
                    "change": change,
                })
                prev_shares = shares
            else:
                # Fund didn't hold this quarter
                if prev_shares is not None and prev_shares > 0:
                    timeline.append({
                        "quarter": q,
                        "shares": 0,
                        "value_usd": 0,
                        "status": "SOLD_OUT",
                        "change": -prev_shares,
                    })
                    prev_shares = 0
                else:
                    timeline.append({
                        "quarter": q,
                        "shares": 0,
                        "value_usd": 0,
                        "status": "NOT_HELD",
                        "change": 0,
                    })

        # Current position (latest quarter)
        latest_q = all_quarters[-1]
        latest = data["quarters"].get(latest_q, {})

        funds.append({
            "cik": cik,
            "name": data["name"],
            "timeline": timeline,
            "current_shares": latest.get("shares", 0),
            "current_value": latest.get("value_usd", 0),
            "first_seen": data["first_seen"],
            "last_seen": data["last_seen"],
            "quarters_held": len(data["quarters"]),
        })

    # Sort by current shares descending
    funds.sort(
        key=lambda f: f["current_shares"] or 0, reverse=True,
    )

    return {
        "ticker": ticker,
        "funds": funds,
        "quarters": all_quarters,
        "count": len(funds),
    }


@app.post("/api/tracker/backfill")
async def tracker_backfill(
    max_quarters: int = Query(default=8, ge=1, le=20),
) -> dict:
    """Trigger historical 13F backfill for all active filers."""
    from app.services.sec_13f_service import SEC13FCollector
    collector = SEC13FCollector()
    result = await collector.backfill_history(max_quarters=max_quarters)
    return {"status": "complete", **result}


def _prior_quarter(quarter: str) -> str | None:
    """Calculate the prior quarter string, e.g. '2025Q1' -> '2024Q4'."""
    if not quarter or len(quarter) < 6:
        return None
    try:
        year = int(quarter[:4])
        q = int(quarter[-1])
        if q == 1:
            return f"{year - 1}Q4"
        return f"{year}Q{q - 1}"
    except (ValueError, IndexError):
        return None


# ══════════════════════════════════════════════════════════════════════
# DATA EXPLORER API — browse, edit, and delete scraped data
# ══════════════════════════════════════════════════════════════════════

# ── Table configuration registry ────────────────────────────────────
_DATA_TABLES: dict[str, dict] = {
    "youtube": {
        "table": "youtube_transcripts",
        "pk": ["ticker", "video_id"],
        "search_cols": ["ticker", "title", "channel"],
        "key_fields": ["ticker", "title"],
        "columns": [
            "ticker", "video_id", "title", "channel", "published_at",
            "duration_seconds", "raw_transcript", "collected_at",
            "scanned_for_tickers",
        ],
        "editable": [
            "ticker", "title", "channel",
        ],
    },
    "reddit": {
        "table": "discovered_tickers",
        "pk": ["rowid"],
        "where_clause": "source = 'reddit'",
        "search_cols": ["ticker", "source_detail", "context_snippet"],
        "key_fields": ["ticker"],
        "columns": [
            "rowid", "ticker", "source", "source_detail",
            "discovery_score", "sentiment_hint", "context_snippet",
            "source_url", "discovered_at",
        ],
        "editable": [
            "ticker", "source_detail", "sentiment_hint", "context_snippet",
        ],
    },
    "13f-filers": {
        "table": "sec_13f_filers",
        "pk": ["cik"],
        "search_cols": ["cik", "filer_name"],
        "key_fields": ["filer_name"],
        "columns": [
            "cik", "filer_name", "latest_quarter", "next_expected_filing",
            "last_checked", "is_active",
        ],
        "editable": ["filer_name"],
    },
    "13f-holdings": {
        "table": "sec_13f_holdings",
        "from_clause": (
            "sec_13f_holdings h "
            "LEFT JOIN sec_13f_filers f ON h.cik = f.cik"
        ),
        "pk": ["cik", "ticker", "filing_quarter"],
        "search_cols": ["h.cik", "h.ticker", "h.name_of_issuer", "f.filer_name"],
        "key_fields": ["ticker", "name_of_issuer"],
        "columns": [
            "f.filer_name AS filer_name", "h.ticker AS ticker",
            "h.name_of_issuer AS name_of_issuer", "h.value_usd AS value_usd",
            "h.shares AS shares", "h.filing_quarter AS filing_quarter",
            "h.filing_date AS filing_date", "h.cik AS cik",
            "h.cusip AS cusip", "h.share_type AS share_type",
            "h.collected_at AS collected_at",
        ],
        "editable": ["ticker", "name_of_issuer", "cusip"],
        "filters": {
            "cik": "h.cik",
            "quarter": "h.filing_quarter",
        },
    },
    "congress": {
        "table": "congressional_trades",
        "pk": ["id"],
        "search_cols": ["member_name", "ticker", "asset_name"],
        "key_fields": ["member_name", "ticker"],
        "columns": [
            "id", "member_name", "chamber", "ticker", "asset_name",
            "tx_type", "tx_date", "filed_date", "amount_range",
            "source_url", "collected_at",
        ],
        "editable": [
            "member_name", "ticker", "asset_name", "tx_type",
            "amount_range",
        ],
    },
}


class DataDeleteRequest(BaseModel):
    ids: list[dict | str]


class DataEditRequest(BaseModel):
    pk: dict
    column: str
    value: str | float | int | bool | None


class DataCleanResult(BaseModel):
    deleted: int


@app.get("/api/data/{table}")
async def data_explorer_list(
    table: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=200),
    search: str = Query(default=""),
    sort: str = Query(default=""),
    order: str = Query(default="desc"),
    cik: str = Query(default=""),
    quarter: str = Query(default=""),
) -> dict:
    """Paginated data listing for the Data Explorer."""
    cfg = _DATA_TABLES.get(table)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Unknown table: {table}")

    tbl = cfg["table"]
    offset = (page - 1) * page_size

    # Build WHERE clauses
    wheres: list[str] = []
    params: list = []
    if cfg.get("where_clause"):
        wheres.append(cfg["where_clause"])

    # Extra filters (e.g. cik, quarter for holdings)
    if cik and "filters" in cfg and "cik" in cfg["filters"]:
        wheres.append(f"{cfg['filters']['cik']} = ?")
        params.append(cik)
    if quarter and "filters" in cfg and "quarter" in cfg["filters"]:
        wheres.append(f"{cfg['filters']['quarter']} = ?")
        params.append(quarter)

    # Search
    if search:
        search_conditions = [
            f"CAST({col} AS VARCHAR) ILIKE ?"
            for col in cfg.get("search_cols", [])
        ]
        if search_conditions:
            wheres.append(f"({' OR '.join(search_conditions)})")
            params.extend([f"%{search}%"] * len(search_conditions))

    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""

    # Sort — support aliased columns (e.g. "h.ticker AS ticker")
    # The frontend sends the alias name; we need to find the matching column spec.
    col_specs = cfg["columns"]
    sort_col = ""
    if sort:
        for spec in col_specs:
            # Extract alias: "h.ticker AS ticker" → "ticker", or bare "ticker" → "ticker"
            alias = spec.split(" AS ")[-1].strip() if " AS " in spec else spec
            if alias == sort:
                sort_col = spec.split(" AS ")[0].strip() if " AS " in spec else spec
                break
    order_dir = "ASC" if order.lower() == "asc" else "DESC"
    order_sql = f"ORDER BY {sort_col} {order_dir}" if sort_col else ""

    # Columns — select specific columns to avoid fetching huge transcript blobs
    select_cols = ", ".join(cfg["columns"])

    # Use from_clause (for JOINs) or plain table name
    from_source = cfg.get("from_clause", tbl)

    try:
        db = get_db()
        # Count
        count_sql = f"SELECT COUNT(*) as cnt FROM {from_source} {where_sql}"
        total = db.execute(count_sql, params).fetchone()[0]

        # Data
        data_sql = (
            f"SELECT {select_cols} FROM {from_source} {where_sql} "
            f"{order_sql} LIMIT ? OFFSET ?"
        )
        result = db.execute(data_sql, params + [page_size, offset]).fetchall()
        cols = [desc[0] for desc in db.description]
        rows = [dict(zip(cols, row)) for row in result]

        # For YouTube, truncate transcript in list view
        if table == "youtube":
            for row in rows:
                transcript = row.get("raw_transcript")
                if transcript and len(transcript) > 150:
                    row["raw_transcript"] = transcript[:150] + "…"

        return {
            "table": table,
            "rows": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, -(-total // page_size)),
        }
    except Exception as e:
        logger.error("Data explorer list error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/data/{table}/row")
async def data_explorer_get_row(
    table: str,
    pk: str = Query(..., description="JSON-encoded primary key"),
) -> dict:
    """Get a single full row (e.g. for modals with full transcript text)."""
    import json as json_mod

    cfg = _DATA_TABLES.get(table)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Unknown table: {table}")

    try:
        pk_dict = json_mod.loads(pk)
    except (json_mod.JSONDecodeError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid pk JSON") from exc

    tbl = cfg["table"]
    pk_cols = cfg["pk"]
    wheres = [f"{col} = ?" for col in pk_cols if col != "rowid"]
    params = [pk_dict[col] for col in pk_cols if col != "rowid"]

    if "rowid" in pk_cols:
        wheres.append("rowid = ?")
        params.append(int(pk_dict["rowid"]))

    extra_where = cfg.get("where_clause")
    if extra_where:
        wheres.append(extra_where)

    where_sql = " AND ".join(wheres) if wheres else "1=1"

    try:
        db = get_db()
        row = db.execute(
            f"SELECT * FROM {tbl} WHERE {where_sql} LIMIT 1", params
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Row not found")
        cols = [desc[0] for desc in db.description]
        return dict(zip(cols, row))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.delete("/api/data/{table}")
async def data_explorer_delete(table: str, req: DataDeleteRequest) -> dict:
    """Delete one or more rows by primary key."""
    cfg = _DATA_TABLES.get(table)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Unknown table: {table}")

    if not req.ids:
        return {"deleted": 0}

    tbl = cfg["table"]
    pk_cols = cfg["pk"]
    deleted = 0

    try:
        db = get_db()
        for pk_val in req.ids:
            if isinstance(pk_val, str):
                pk_val = {pk_cols[0]: pk_val}

            wheres = []
            params = []
            for col in pk_cols:
                if col == "rowid":
                    wheres.append("rowid = ?")
                    params.append(int(pk_val.get("rowid", 0)))
                else:
                    wheres.append(f"{col} = ?")
                    params.append(pk_val.get(col, ""))

            extra_where = cfg.get("where_clause")
            if extra_where:
                wheres.append(extra_where)

            where_sql = " AND ".join(wheres)
            result = db.execute(
                f"DELETE FROM {tbl} WHERE {where_sql}", params
            )
            deleted += result.rowcount if hasattr(result, "rowcount") else 1

        db.commit()
        logger.info("[DataExplorer] Deleted %d rows from %s", deleted, tbl)
        return {"deleted": deleted}
    except Exception as e:
        logger.error("Data explorer delete error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.put("/api/data/{table}")
async def data_explorer_edit(table: str, req: DataEditRequest) -> dict:
    """Inline edit a single cell value."""
    cfg = _DATA_TABLES.get(table)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Unknown table: {table}")

    if req.column not in cfg.get("editable", []):
        raise HTTPException(
            status_code=400,
            detail=f"Column '{req.column}' is not editable",
        )

    tbl = cfg["table"]
    pk_cols = cfg["pk"]

    wheres = []
    params: list = [req.value]
    for col in pk_cols:
        if col == "rowid":
            wheres.append("rowid = ?")
            params.append(int(req.pk.get("rowid", 0)))
        else:
            wheres.append(f"{col} = ?")
            params.append(req.pk.get(col, ""))

    extra_where = cfg.get("where_clause")
    if extra_where:
        wheres.append(extra_where)

    where_sql = " AND ".join(wheres)

    try:
        db = get_db()
        db.execute(
            f"UPDATE {tbl} SET {req.column} = ? WHERE {where_sql}",
            params,
        )
        db.commit()
        logger.info(
            "[DataExplorer] Updated %s.%s for pk=%s",
            tbl, req.column, req.pk,
        )
        return {"status": "updated", "column": req.column}
    except Exception as e:
        logger.error("Data explorer edit error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/data/{table}/clean")
async def data_explorer_clean(table: str) -> dict:
    """Delete rows where key fields are blank or null.

    For the reddit table, also purges rows with RSS-sourced junk
    (source_detail containing 'news articles' instead of real reddit data).
    """
    cfg = _DATA_TABLES.get(table)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Unknown table: {table}")

    tbl = cfg["table"]
    key_fields = cfg.get("key_fields", [])
    if not key_fields:
        return {"deleted": 0}

    # Build condition: any key field is NULL or empty
    conditions = [
        f"({col} IS NULL OR TRIM(CAST({col} AS VARCHAR)) = '')"
        for col in key_fields
    ]
    where_clause = " OR ".join(conditions)

    extra_where = cfg.get("where_clause")
    if extra_where:
        where_clause = f"({where_clause}) AND {extra_where}"

    try:
        db = get_db()
        total_deleted = 0

        # Standard blank-field cleanup
        count_result = db.execute(
            f"SELECT COUNT(*) FROM {tbl} WHERE {where_clause}"
        ).fetchone()
        count = count_result[0] if count_result else 0

        if count > 0:
            db.execute(f"DELETE FROM {tbl} WHERE {where_clause}")
            total_deleted += count
            logger.info(
                "[DataExplorer] Cleaned %d blank rows from %s", count, tbl,
            )

        # Reddit-specific: also purge RSS-sourced junk
        if table == "reddit":
            junk_where = (
                "source = 'reddit' "
                "AND source_detail LIKE '%news articles%'"
            )
            junk_result = db.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE {junk_where}"
            ).fetchone()
            junk_count = junk_result[0] if junk_result else 0

            if junk_count > 0:
                db.execute(f"DELETE FROM {tbl} WHERE {junk_where}")
                total_deleted += junk_count
                logger.info(
                    "[DataExplorer] Purged %d RSS-sourced junk rows from reddit",
                    junk_count,
                )

        if total_deleted > 0:
            db.commit()

        return {"deleted": total_deleted}
    except Exception as e:
        logger.error("Data explorer clean error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
