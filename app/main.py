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


class LLMConfigRequest(BaseModel):
    provider: str | None = None
    ollama_url: str | None = None
    lmstudio_url: str | None = None
    model: str | None = None
    context_size: int | None = None
    temperature: float | None = None


# ── Singleton services ──────────────────────────────────────────────
pipeline = PipelineService()


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
    """Get the current watchlist."""
    wl_path = settings.USER_CONFIG_DIR / "watchlist.json"
    if wl_path.exists():
        data = json.loads(wl_path.read_text(encoding="utf-8"))
        return data
    return {"tickers": []}


@app.put("/api/watchlist")
async def update_watchlist(req: WatchlistUpdateRequest) -> dict:
    """Update the watchlist."""
    wl_path = settings.USER_CONFIG_DIR / "watchlist.json"
    data = {"tickers": [t.upper().strip() for t in req.tickers if t.strip()]}
    wl_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.info("Watchlist updated: %s", data["tickers"])
    return data


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
    """Save new LLM settings + hot-patch the running config."""
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    merged = settings.update_llm_config(data)
    logger.info("LLM config updated: provider=%s model=%s", merged.get("provider"), merged.get("model"))
    return {"status": "updated", "config": merged}


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
async def get_quotes(tickers: str = Query(..., description="Comma-separated ticker symbols")) -> dict:
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(symbols), 8)) as pool:
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
            "SELECT * FROM price_history WHERE ticker = ? "
            "ORDER BY date DESC LIMIT 5",
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
            "SELECT * FROM technicals WHERE ticker = ? "
            "ORDER BY date DESC LIMIT 1",
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
            "SELECT * FROM technicals WHERE ticker = ? "
            "ORDER BY date DESC LIMIT 30",
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
        "price_history", "fundamentals", "financial_history",
        "technicals", "news_articles", "youtube_transcripts",
        "risk_metrics", "balance_sheet", "cash_flows",
        "analyst_data", "insider_activity", "earnings_calendar",
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
