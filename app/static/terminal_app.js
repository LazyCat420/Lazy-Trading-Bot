const { useState, useEffect, useRef, useCallback } = React;
const { createRoot } = ReactDOM;
const { HashRouter, Routes, Route, Link, useNavigate, useParams } = ReactRouterDOM;

// ***************************************************************
// UTILITIES
// ***************************************************************

const fmt = {
    usd: (v) => v == null ? "N/A" : "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
    usdShort: (v) => {
        if (v == null) return "N/A";
        const n = Number(v);
        if (Math.abs(n) >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
        if (Math.abs(n) >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
        if (Math.abs(n) >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
        return "$" + n.toLocaleString("en-US", { maximumFractionDigits: 0 });
    },
    pct: (v) => v == null ? "N/A" : (Number(v) * 100).toFixed(2) + "%",
    pctRaw: (v) => v == null ? "N/A" : Number(v).toFixed(2) + "%",
    num: (v, d = 2) => v == null ? "N/A" : Number(v).toFixed(d),
    date: (v) => {
        if (!v) return "N/A";
        const d = new Date(v);
        return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    },
    ago: (v) => {
        if (!v) return "";
        const ms = Date.now() - new Date(v).getTime();
        const mins = Math.floor(ms / 60000);
        if (mins < 60) return `${mins}m ago`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs}h ago`;
        return `${Math.floor(hrs / 24)}d ago`;
    },
};

const changeColor = (val) => {
    if (val == null) return "text-gray-500";
    return Number(val) >= 0 ? "text-green-400" : "text-red-400";
};

const signalColor = (signal) => {
    if (!signal) return "text-gray-500";
    const s = signal.toUpperCase();
    if (s.includes("BUY") || s.includes("BULLISH")) return "text-green-400";
    if (s.includes("SELL") || s.includes("BEARISH")) return "text-red-400";
    return "text-yellow-400";
};

const Spinner = () => (
    <div className="flex items-center justify-center p-8">
        <span className="material-symbols-outlined text-primary animate-spin text-3xl">progress_activity</span>
    </div>
);

const Skeleton = ({ w = "100%", h = "1rem" }) => (
    <div className="skeleton" style={{ width: w, height: h }}></div>
);

const MetricRow = ({ label, value, sub }) => (
    <div className="flex justify-between items-center py-2 border-b border-border-dark/50">
        <span className="text-text-muted text-xs">{label}</span>
        <div className="text-right">
            <span className="text-white text-sm font-mono">{value ?? "N/A"}</span>
            {sub && <div className="text-[10px] text-text-muted">{sub}</div>}
        </div>
    </div>
);

// ***************************************************************
// DATA HOOK  Central state management
// ***************************************************************

const useTerminalData = () => {
    const [watchlist, setWatchlist] = useState([]);
    const [selectedTicker, setSelectedTicker] = useState("NVDA");
    const [overviewCache, setOverviewCache] = useState({});
    const [loading, setLoading] = useState(true);
    const [analyzing, setAnalyzing] = useState(false);
    const [analysisResult, setAnalysisResult] = useState(null);
    const [error, setError] = useState(null);

    // ── Streaming analysis state ──
    const [streamSteps, setStreamSteps] = useState({});   // {name: {status, ...}}
    const [streamAgents, setStreamAgents] = useState({});  // {name: {report}}
    const [streamDecision, setStreamDecision] = useState(null);
    const [streamErrors, setStreamErrors] = useState([]);
    const [streamPlan, setStreamPlan] = useState(null);    // {steps, agents, has_decision}
    const [streamPhase, setStreamPhase] = useState("");    // "data", "agents", "decision", "done"
    const [cachedDate, setCachedDate] = useState(null);    // date string from cached reports

    // Load watchlist on mount
    useEffect(() => {
        const init = async () => {
            try {
                const res = await fetch("/api/watchlist");
                const data = await res.json();
                const tickers = data.tickers || [];
                setWatchlist(tickers);
                if (tickers.length > 0 && !tickers.includes(selectedTicker)) {
                    setSelectedTicker(tickers[0]);
                }
            } catch (e) {
                console.error("Init error:", e);
                setError(e.message);
            } finally {
                setLoading(false);
            }
        };
        init();
    }, []);

    // Fetch overview for each ticker
    const fetchOverview = useCallback(async (ticker) => {
        // Use a ref-like check via setState to avoid stale closure reads
        try {
            const res = await fetch(`/api/dashboard/overview/${ticker}`);
            const data = await res.json();
            setOverviewCache(prev => {
                if (prev[ticker]) return prev; // already cached, skip
                return { ...prev, [ticker]: data };
            });
            return data;
        } catch (e) {
            console.error("Overview fetch error:", e);
            return null;
        }
    }, []);

    // Fetch all overviews for watchlist
    useEffect(() => {
        if (watchlist.length === 0) return;
        watchlist.forEach(t => fetchOverview(t));
    }, [watchlist]);

    // Legacy run analysis (POST, all-at-once)
    const runAnalysis = useCallback(async (ticker, mode = "full") => {
        setAnalyzing(true);
        setAnalysisResult(null);
        try {
            const res = await fetch("/api/analyze", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ ticker, mode }),
            });
            const data = await res.json();
            setAnalysisResult(data);
            return data;
        } catch (e) {
            console.error("Analysis error:", e);
            setError(e.message);
            return null;
        } finally {
            setAnalyzing(false);
        }
    }, []);

    // ── NEW: Streaming analysis via SSE ──
    const abortRef = useRef(null);
    const runAnalysisStream = useCallback(async (ticker, mode = "full") => {
        // Abort any existing stream
        if (abortRef.current) abortRef.current.abort();
        const controller = new AbortController();
        abortRef.current = controller;

        // Reset state
        setAnalyzing(true);
        setAnalysisResult(null);
        setStreamSteps({});
        setStreamAgents({});
        setStreamDecision(null);
        setStreamErrors([]);
        setStreamPlan(null);
        setStreamPhase("data");

        try {
            const res = await fetch(
                `/api/analyze-stream?ticker=${encodeURIComponent(ticker)}&mode=${encodeURIComponent(mode)}`,
                { signal: controller.signal },
            );

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                // Parse SSE lines
                const lines = buffer.split("\n");
                buffer = lines.pop(); // keep incomplete line in buffer

                for (const line of lines) {
                    if (!line.startsWith("data: ")) continue;
                    try {
                        const event = JSON.parse(line.slice(6));
                        switch (event.type) {
                            case "plan":
                                setStreamPlan(event);
                                // Initialize all steps as pending
                                const initSteps = {};
                                (event.steps || []).forEach(s => { initSteps[s] = { status: "pending" }; });
                                setStreamSteps(initSteps);
                                break;
                            case "step_start":
                                setStreamSteps(prev => ({ ...prev, [event.name]: { status: "running" } }));
                                break;
                            case "step_complete":
                                setStreamSteps(prev => ({ ...prev, [event.name]: { status: "ok", ...event } }));
                                break;
                            case "step_error":
                                setStreamSteps(prev => ({ ...prev, [event.name]: { status: "error", error: event.error } }));
                                setStreamErrors(prev => [...prev, `${event.name}: ${event.error}`]);
                                break;
                            case "agent_start":
                                setStreamPhase("agents");
                                setStreamAgents(prev => ({ ...prev, [event.name]: { status: "running" } }));
                                break;
                            case "agent_complete":
                                setStreamAgents(prev => ({ ...prev, [event.name]: { status: "ok", report: event.report } }));
                                break;
                            case "agent_error":
                                setStreamAgents(prev => ({ ...prev, [event.name]: { status: "error", error: event.error } }));
                                setStreamErrors(prev => [...prev, `Agent ${event.name}: ${event.error}`]);
                                break;
                            case "decision_complete":
                                setStreamPhase("decision");
                                setStreamDecision(event.decision);
                                break;
                            case "decision_error":
                                setStreamErrors(prev => [...prev, `Decision: ${event.error}`]);
                                break;
                            case "done":
                                setStreamPhase("done");
                                break;
                            case "error":
                                setStreamErrors(prev => [...prev, event.error]);
                                break;
                        }
                    } catch (parseErr) {
                        console.warn("SSE parse error:", parseErr, line);
                    }
                }
            }
        } catch (e) {
            if (e.name !== "AbortError") {
                console.error("Stream error:", e);
                setStreamErrors(prev => [...prev, e.message]);
            }
        } finally {
            setAnalyzing(false);
            setStreamPhase("done");
        }
    }, []);

    // ── Load cached analysis from disk (instant, no LLM) ──
    const loadCachedAnalysis = useCallback(async (ticker) => {
        try {
            const res = await fetch(`/api/dashboard/analysis/${encodeURIComponent(ticker)}`);
            const data = await res.json();
            if (data.cached && data.agents) {
                setStreamAgents(data.agents);
                if (data.decision) setStreamDecision(data.decision);
                setStreamPhase("done");
                setCachedDate(data.date || null);
                return true; // had cached data
            }
        } catch (e) {
            console.error("Cached analysis load error:", e);
        }
        return false;
    }, []);

    // Watchlist management
    const addTicker = useCallback(async (ticker) => {
        const t = ticker.toUpperCase().trim();
        if (!t || watchlist.includes(t)) return;
        const updated = [...watchlist, t];
        setWatchlist(updated);
        await fetch("/api/watchlist", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tickers: updated }),
        });
        fetchOverview(t);
    }, [watchlist, fetchOverview]);

    const removeTicker = useCallback(async (ticker) => {
        const updated = watchlist.filter(t => t !== ticker);
        setWatchlist(updated);
        await fetch("/api/watchlist", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tickers: updated }),
        });
    }, [watchlist]);

    return {
        watchlist, selectedTicker, setSelectedTicker,
        overviewCache, fetchOverview, loading, error,
        analyzing, analysisResult, runAnalysis,
        addTicker, removeTicker,
        // Streaming state
        runAnalysisStream, streamSteps, streamAgents,
        streamDecision, streamErrors, streamPlan, streamPhase,
        // Cached analysis
        loadCachedAnalysis, cachedDate,
    };
};

// ***************************************************************
// CHART WIDGET  Lightweight Charts candlestick
// ***************************************************************

const ChartWidget = ({ symbol, height = 400 }) => {
    const containerRef = useRef(null);
    const chartRef = useRef(null);

    useEffect(() => {
        if (!containerRef.current || !symbol) return;

        const loadChart = async () => {
            try {
                const res = await fetch(`/api/dashboard/prices/${symbol}?days=365`);
                const json = await res.json();
                const prices = json.prices || [];
                if (prices.length === 0) return;

                // Clean up old chart
                if (chartRef.current) {
                    chartRef.current.remove();
                    chartRef.current = null;
                }

                const chart = LightweightCharts.createChart(containerRef.current, {
                    width: containerRef.current.clientWidth,
                    height: height,
                    layout: {
                        background: { color: "#0f1115" },
                        textColor: "#5f746b",
                        fontFamily: "JetBrains Mono, monospace",
                        fontSize: 11,
                    },
                    grid: {
                        vertLines: { color: "rgba(40,57,50,0.3)" },
                        horzLines: { color: "rgba(40,57,50,0.3)" },
                    },
                    crosshair: {
                        mode: 0,
                        vertLine: { color: "#13ec99", width: 1, style: 2, labelBackgroundColor: "#13ec99" },
                        horzLine: { color: "#13ec99", width: 1, style: 2, labelBackgroundColor: "#13ec99" },
                    },
                    rightPriceScale: { borderColor: "#283932" },
                    timeScale: { borderColor: "#283932", timeVisible: false },
                });

                chartRef.current = chart;

                // Candlestick series
                const candleSeries = chart.addCandlestickSeries({
                    upColor: "#22c55e",
                    downColor: "#ef4444",
                    borderUpColor: "#22c55e",
                    borderDownColor: "#ef4444",
                    wickUpColor: "#22c55e",
                    wickDownColor: "#ef4444",
                });

                const ohlc = prices.map(p => ({
                    time: p.date,
                    open: p.open,
                    high: p.high,
                    low: p.low,
                    close: p.close,
                }));
                candleSeries.setData(ohlc);

                // Volume
                const volSeries = chart.addHistogramSeries({
                    color: "rgba(19,236,153,0.15)",
                    priceFormat: { type: "volume" },
                    priceScaleId: "vol",
                });
                chart.priceScale("vol").applyOptions({
                    scaleMargins: { top: 0.85, bottom: 0 },
                });
                volSeries.setData(prices.map(p => ({
                    time: p.date,
                    value: p.volume || 0,
                    color: p.close >= p.open ? "rgba(34,197,94,0.2)" : "rgba(239,68,68,0.2)",
                })));

                // SMA 20
                const sma20 = [];
                for (let i = 19; i < prices.length; i++) {
                    let sum = 0;
                    for (let j = i - 19; j <= i; j++) sum += prices[j].close;
                    sma20.push({ time: prices[i].date, value: sum / 20 });
                }
                if (sma20.length > 0) {
                    const sma20Series = chart.addLineSeries({ color: "#3b82f6", lineWidth: 1 });
                    sma20Series.setData(sma20);
                }

                // SMA 50
                const sma50 = [];
                for (let i = 49; i < prices.length; i++) {
                    let sum = 0;
                    for (let j = i - 49; j <= i; j++) sum += prices[j].close;
                    sma50.push({ time: prices[i].date, value: sum / 50 });
                }
                if (sma50.length > 0) {
                    const sma50Series = chart.addLineSeries({ color: "#f59e0b", lineWidth: 1 });
                    sma50Series.setData(sma50);
                }

                chart.timeScale().fitContent();

                // Resize handler
                const handleResize = () => {
                    if (containerRef.current && chartRef.current) {
                        chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
                    }
                };
                window.addEventListener("resize", handleResize);
                return () => window.removeEventListener("resize", handleResize);

            } catch (e) {
                console.error("Chart load error:", e);
            }
        };

        loadChart();

        return () => {
            if (chartRef.current) {
                chartRef.current.remove();
                chartRef.current = null;
            }
        };
    }, [symbol, height]);

    return <div ref={containerRef} className="w-full rounded border border-border-dark bg-onyx-black" />;
};

// ***************************************************************
// TICKER DETAIL PANEL  Expandable row with tabs
// ***************************************************************

// ***************************************************************
// DATA EXPLORER PANEL (TickerDetailPanel) — Reusable & Progressive
// ***************************************************************

const TickerDetailPanel = ({ ticker, streamSignals = {} }) => {
    const [tab, setTab] = useState("OV");
    const [overview, setOverview] = useState(null);
    const [news, setNews] = useState([]);
    const [technicals, setTechnicals] = useState(null);
    const [financials, setFinancials] = useState(null);

    // Track loading states individually
    const [loadingOv, setLoadingOv] = useState(true);
    const [loadingNews, setLoadingNews] = useState(true);
    const [loadingTech, setLoadingTech] = useState(true);
    const [loadingFin, setLoadingFin] = useState(true);

    // Serialize streamSignals to a stable string to avoid re-render loops
    // (object reference changes every render even if contents are the same)
    const signalsKey = JSON.stringify(streamSignals);

    // Initial load (if no stream signals, e.g. normal viewing)
    useEffect(() => {
        if (Object.keys(streamSignals).length === 0) {
            fetchAll();
        }
    }, [ticker]);  // Only re-run on ticker change, not on every render

    // Reactive fetching based on stream signals
    useEffect(() => {
        if (streamSignals.price_history === "ok") fetchOverview();
        if (streamSignals.news === "ok" || streamSignals.news_scrape === "ok") fetchNews();
        if (streamSignals.technicals === "ok") fetchTechnicals();
        if (streamSignals.financial_history === "ok" || streamSignals.balance_sheet === "ok") fetchFinancials();
        if (streamSignals.fundamentals === "ok") fetchOverview(); // Fundamentals are in overview
    }, [signalsKey, ticker]);

    const fetchAll = () => {
        fetchOverview();
        fetchNews();
        fetchTechnicals();
        fetchFinancials();
    };

    const fetchOverview = async () => {
        try {
            const res = await fetch(`/api/dashboard/overview/${ticker}`);
            if (res.ok) {
                const data = await res.json();
                setOverview(data);
            }
        } catch (e) { console.error(e); } finally { setLoadingOv(false); }
    };

    const fetchNews = async () => {
        try {
            const res = await fetch(`/api/dashboard/news/${ticker}`);
            if (res.ok) {
                const data = await res.json();
                setNews(data.articles || []);
            }
        } catch (e) { console.error(e); } finally { setLoadingNews(false); }
    };

    const fetchTechnicals = async () => {
        try {
            const res = await fetch(`/api/dashboard/technicals/${ticker}`);
            if (res.ok) {
                const data = await res.json();
                setTechnicals(data.technicals?.[0] || null);
            }
        } catch (e) { console.error(e); } finally { setLoadingTech(false); }
    };

    const fetchFinancials = async () => {
        try {
            const res = await fetch(`/api/dashboard/financials/${ticker}`);
            if (res.ok) {
                setFinancials(await res.json());
            }
        } catch (e) { console.error(e); } finally { setLoadingFin(false); }
    };

    const TabBtn = ({ id, label, icon }) => (
        <button onClick={() => setTab(id)}
            className={`tab-btn flex items-center gap-1.5 ${tab === id ? "active" : ""}`}>
            <span className="material-symbols-outlined text-sm">{icon}</span>
            {label}
        </button>
    );

    const fundas = overview?.fundamentals || {};
    const rsi = technicals?.rsi_14 || technicals?.RSI_14 || null;

    return (
        <div className="bg-onyx-panel border-t border-border-dark animate-fadeIn h-full flex flex-col">
            <div className="flex border-b border-border-dark px-6 bg-onyx-surface shrink-0">
                <TabBtn id="OV" label="Overview" icon="dashboard" />
                <TabBtn id="NEWS" label="News" icon="newspaper" />
                <TabBtn id="FUND" label="Fundamentals" icon="account_balance" />
                <TabBtn id="TECH" label="Technicals" icon="show_chart" />
            </div>

            <div className="p-6 flex-1 overflow-y-auto">
                {tab === "OV" && (
                    <div className="grid grid-cols-12 gap-6">
                        <div className="col-span-8">
                            <div className="glass-card p-4 h-[320px] flex flex-col">
                                <h4 className="text-xs text-text-muted uppercase mb-2 flex justify-between">
                                    <span>Price History</span>
                                    {loadingOv && <span className="animate-pulse text-primary">Live Updating...</span>}
                                </h4>
                                {loadingOv && !overview ? (
                                    <div className="flex-1 flex items-center justify-center">
                                        <span className="material-symbols-outlined text-3xl animate-spin text-text-muted">progress_activity</span>
                                    </div>
                                ) : (
                                    <ChartWidget symbol={ticker} height={280} />
                                )}
                            </div>
                        </div>
                        <div className="col-span-4 flex flex-col gap-4">
                            <div className="glass-card p-4">
                                <h4 className="text-xs text-text-muted uppercase mb-3 flex items-center gap-2">
                                    Key Metrics
                                    {loadingOv && <span className="material-symbols-outlined text-[10px] animate-spin">sync</span>}
                                </h4>
                                <MetricRow label="Market Cap" value={fmt.usdShort(fundas.market_cap)} />
                                <MetricRow label="P/E Ratio" value={fmt.num(fundas.trailing_pe)} />
                                <MetricRow label="Fwd P/E" value={fmt.num(fundas.forward_pe)} />
                                <MetricRow label="EPS" value={fmt.usd(fundas.trailing_eps)} />
                                <MetricRow label="Revenue" value={fmt.usdShort(fundas.revenue)} />
                                <MetricRow label="Margin" value={fmt.pct(fundas.profit_margin)} />
                            </div>
                            {rsi != null && (
                                <div className="glass-card p-4">
                                    <div className="flex justify-between items-center mb-2">
                                        <span className="text-xs text-text-muted uppercase">RSI (14)</span>
                                        <span className={`text-lg font-bold font-mono ${rsi > 70 ? "text-red-400" : rsi < 30 ? "text-green-400" : "text-white"}`}>
                                            {fmt.num(rsi, 1)}
                                        </span>
                                    </div>
                                    <div className="progress-bar">
                                        <div className="progress-bar-fill" style={{
                                            width: `${Math.min(rsi, 100)}%`,
                                            background: rsi > 70 ? "#ef4444" : rsi < 30 ? "#22c55e" : "#13ec99"
                                        }} />
                                    </div>
                                    <div className="flex justify-between text-[10px] text-text-muted mt-1">
                                        <span>Oversold</span><span>Overbought</span>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                )}

                {tab === "NEWS" && (
                    <div className="space-y-4">
                        {loadingNews && news.length === 0 && (
                            <div className="text-center py-8 text-text-muted">
                                <span className="material-symbols-outlined animate-spin text-2xl mb-2">progress_activity</span>
                                <p>Fetching news...</p>
                            </div>
                        )}
                        {news.map((item, i) => (
                            <div key={i} className="glass-card p-3 flex gap-3 hover:bg-white/5 transition">
                                <div className="flex-1">
                                    <a href={item.url} target="_blank" rel="noopener noreferrer" className="text-sm font-bold text-white hover:text-primary mb-1 block">
                                        {item.title}
                                    </a>
                                    <div className="flex items-center gap-2 text-[10px] text-text-muted mb-2">
                                        <span className="font-mono">{item.source || item.publisher}</span>
                                        <span>•</span>
                                        <span>{new Date(item.published_at).toLocaleString()}</span>
                                    </div>
                                    <p className="text-xs text-text-secondary line-clamp-2">{item.summary}</p>
                                </div>
                            </div>
                        ))}
                    </div>
                )}


                {tab === "FUND" && financials && (
                    <div className="grid grid-cols-2 gap-8">
                        <div>
                            <h4 className="text-xs font-bold text-primary uppercase mb-3 border-b border-border-dark pb-2">Valuation</h4>
                            <MetricRow label="Market Cap" value={fmt.usdShort(fundas.market_cap)} />
                            <MetricRow label="P/E Ratio" value={fmt.num(fundas.trailing_pe)} />
                            <MetricRow label="Forward P/E" value={fmt.num(fundas.forward_pe)} />
                            <MetricRow label="PEG Ratio" value={fmt.num(fundas.peg_ratio)} />
                            <MetricRow label="P/S Ratio" value={fmt.num(fundas.price_to_sales)} />
                            <MetricRow label="P/B Ratio" value={fmt.num(fundas.price_to_book)} />
                            <MetricRow label="EV/EBITDA" value={fmt.num(fundas.ev_to_ebitda)} />
                        </div>
                        <div>
                            <h4 className="text-xs font-bold text-primary uppercase mb-3 border-b border-border-dark pb-2">Performance</h4>
                            <MetricRow label="Revenue" value={fmt.usdShort(fundas.revenue)} />
                            <MetricRow label="Rev Growth" value={fmt.pct(fundas.revenue_growth)} />
                            <MetricRow label="Net Income" value={fmt.usdShort(fundas.net_income)} />
                            <MetricRow label="Profit Margin" value={fmt.pct(fundas.profit_margin)} />
                            <MetricRow label="ROE" value={fmt.pct(fundas.return_on_equity)} />
                            <MetricRow label="ROA" value={fmt.pct(fundas.return_on_assets)} />
                            <MetricRow label="EPS" value={fmt.usd(fundas.trailing_eps)} />
                        </div>
                    </div>
                )}

                {tab === "TECH" && technicals && (
                    <div className="grid grid-cols-3 gap-6">
                        <div className="glass-card p-4 text-center">
                            <div className={`text-3xl font-bold font-mono mb-1 ${rsi > 70 ? "text-red-400" : rsi < 30 ? "text-green-400" : "text-white"}`}>
                                {fmt.num(rsi, 1)}
                            </div>
                            <div className="text-xs text-text-muted uppercase">RSI (14)</div>
                        </div>
                        <div className="glass-card p-4 text-center">
                            <div className="text-3xl font-bold font-mono text-white mb-1">
                                {fmt.num(technicals?.MACD_12_26_9 || technicals?.macd_12_26_9, 2)}
                            </div>
                            <div className="text-xs text-text-muted uppercase">MACD</div>
                        </div>
                        <div className="glass-card p-4 text-center">
                            <div className="text-3xl font-bold font-mono text-white mb-1">
                                {fmt.num(technicals?.ATRr_14 || technicals?.atr_14, 2)}
                            </div>
                            <div className="text-xs text-text-muted uppercase">ATR (14)</div>
                        </div>
                        <div className="col-span-3">
                            <h4 className="text-xs font-bold text-primary uppercase mb-3">Moving Averages</h4>
                            <div className="grid grid-cols-4 gap-3">
                                {[
                                    ["SMA 20", technicals?.SMA_20 || technicals?.sma_20],
                                    ["SMA 50", technicals?.SMA_50 || technicals?.sma_50],
                                    ["SMA 200", technicals?.SMA_200 || technicals?.sma_200],
                                    ["EMA 9", technicals?.EMA_9 || technicals?.ema_9],
                                ].map(([name, val]) => (
                                    <div key={name} className="glass-card p-3 text-center">
                                        <div className="text-sm font-mono text-white">{fmt.num(val)}</div>
                                        <div className="text-[10px] text-text-muted mt-1">{name}</div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};

// ***************************************************************
// WATCHLIST PAGE  Home screen
// ***************************************************************

const WatchlistPage = ({
    watchlist, selectedTicker, setSelectedTicker,
    overviewCache, fetchOverview,
    addTicker, removeTicker, runAnalysis, analyzing,
}) => {
    const navigate = useNavigate();
    const [expandedRow, setExpandedRow] = useState(null);
    const [addInput, setAddInput] = useState("");

    const handleAdd = () => {
        if (addInput.trim()) {
            addTicker(addInput);
            setAddInput("");
        }
    };

    return (
        <div className="flex h-full w-full bg-onyx-black text-gray-200 font-display">
            {/* Sidebar */}
            <aside className="w-64 bg-onyx-panel border-r border-border-dark flex flex-col shrink-0 h-full">
                <div className="h-16 flex items-center px-4 border-b border-border-dark">
                    <div className="flex items-center gap-2">
                        <div className="w-8 h-8 bg-primary/20 rounded flex items-center justify-center">
                            <span className="material-symbols-outlined text-primary text-xl">smart_toy</span>
                        </div>
                        <div>
                            <h1 className="text-white text-base font-bold leading-none tracking-tight">LAZY BOT</h1>
                            <p className="text-text-secondary text-[10px] font-mono mt-1">v1.0  Terminal</p>
                        </div>
                    </div>
                </div>

                <div className="flex-1 overflow-y-auto py-4 px-2 flex flex-col gap-1">
                    <h3 className="px-2 text-xs font-mono text-text-muted uppercase tracking-wider mb-2">Navigation</h3>

                    <Link to="/" className="flex items-center gap-3 px-3 py-2 rounded bg-border-dark/50 border-l-2 border-primary group">
                        <span className="material-symbols-outlined text-primary text-[20px]">monitoring</span>
                        <span className="text-sm font-medium text-white">Watchlist</span>
                    </Link>

                    <Link to="/settings" className="flex items-center gap-3 px-3 py-2 rounded hover:bg-border-dark/50 group transition-colors">
                        <span className="material-symbols-outlined text-text-secondary text-[20px]">tune</span>
                        <span className="text-sm font-medium text-text-secondary group-hover:text-white">Settings</span>
                    </Link>

                    <Link to="/diagnostics" className="flex items-center gap-3 px-3 py-2 rounded hover:bg-border-dark/50 group transition-colors">
                        <span className="material-symbols-outlined text-text-secondary text-[20px]">bug_report</span>
                        <span className="text-sm font-medium text-text-secondary group-hover:text-white">Diagnostics</span>
                    </Link>

                    <div className="mt-6">
                        <h3 className="px-2 text-xs font-mono text-text-muted uppercase tracking-wider mb-2">Watchlist</h3>
                        {watchlist.map(t => (
                            <button key={t} onClick={() => { setSelectedTicker(t); setExpandedRow(expandedRow === t ? null : t); }}
                                className={`w-full flex items-center justify-between px-3 py-2 rounded transition-colors ${selectedTicker === t ? "bg-border-dark/50 text-primary" : "text-text-secondary hover:bg-border-dark/30 hover:text-white"
                                    }`}>
                                <span className="text-sm font-mono">{t}</span>
                                {overviewCache[t]?.price?.close && (
                                    <span className="text-[10px] font-mono">${Number(overviewCache[t].price.close).toFixed(2)}</span>
                                )}
                            </button>
                        ))}
                    </div>
                </div>
            </aside>

            {/* Main Content */}
            <main className="flex-1 flex flex-col overflow-hidden">
                {/* Header Bar */}
                <div className="h-14 flex items-center justify-between px-6 border-b border-border-dark bg-onyx-panel shrink-0">
                    <div className="flex items-center gap-4">
                        <h2 className="text-white font-bold text-lg">Watchlist</h2>
                        <span className="text-text-muted text-xs font-mono">{watchlist.length} tickers</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <input
                            type="text" value={addInput} onChange={e => setAddInput(e.target.value.toUpperCase())}
                            onKeyDown={e => e.key === "Enter" && handleAdd()}
                            placeholder="Add ticker|" maxLength={10}
                            className="w-32 bg-onyx-black border border-border-dark rounded px-3 py-1.5 text-xs text-white focus:border-primary focus:outline-none font-mono"
                        />
                        <button onClick={handleAdd}
                            className="px-3 py-1.5 bg-primary/20 hover:bg-primary/30 text-primary text-xs font-bold rounded transition">
                            ADD
                        </button>
                    </div>
                </div>

                {/* Ticker Table */}
                <div className="flex-1 overflow-y-auto">
                    <table className="w-full">
                        <thead className="sticky top-0 bg-onyx-panel z-10">
                            <tr className="text-[10px] text-text-muted uppercase tracking-wider border-b border-border-dark">
                                <th className="text-left px-6 py-3">Ticker</th>
                                <th className="text-right px-4 py-3">Price</th>
                                <th className="text-right px-4 py-3">Change</th>
                                <th className="text-right px-4 py-3">Market Cap</th>
                                <th className="text-right px-4 py-3">RSI</th>
                                <th className="text-center px-4 py-3">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {watchlist.map(ticker => {
                                const ov = overviewCache[ticker] || {};
                                const price = ov.price?.close;
                                const prevClose = ov.prev_price?.close;
                                const change = price && prevClose ? ((price - prevClose) / prevClose) * 100 : null;
                                const rsi = ov.technicals?.rsi_14 || ov.technicals?.RSI_14;

                                return (
                                    <React.Fragment key={ticker}>
                                        <tr onClick={() => { setSelectedTicker(ticker); setExpandedRow(expandedRow === ticker ? null : ticker); }}
                                            className={`border-b border-border-dark/50 hover:bg-onyx-surface cursor-pointer transition-colors ${selectedTicker === ticker ? "bg-onyx-surface" : ""
                                                }`}>
                                            <td className="px-6 py-3">
                                                <div className="flex items-center gap-3">
                                                    <span className={`material-symbols-outlined text-sm ${expandedRow === ticker ? "text-primary" : "text-text-muted"}`}>
                                                        {expandedRow === ticker ? "expand_less" : "expand_more"}
                                                    </span>
                                                    <span className="text-white font-bold font-mono text-sm">{ticker}</span>
                                                </div>
                                            </td>
                                            <td className="text-right px-4 py-3">
                                                <span className="text-white font-mono text-sm">{price ? fmt.usd(price) : <Skeleton w="60px" />}</span>
                                            </td>
                                            <td className="text-right px-4 py-3">
                                                {change != null ? (
                                                    <span className={`metric-pill ${change >= 0 ? "green" : "red"}`}>
                                                        {change >= 0 ? "-2" : "-1/4"} {Math.abs(change).toFixed(2)}%
                                                    </span>
                                                ) : <Skeleton w="50px" />}
                                            </td>
                                            <td className="text-right px-4 py-3 text-text-secondary text-xs font-mono">
                                                {ov.fundamentals?.market_cap ? fmt.usdShort(ov.fundamentals.market_cap) : "-"}
                                            </td>
                                            <td className="text-right px-4 py-3">
                                                {rsi != null ? (
                                                    <span className={`text-xs font-mono font-bold ${rsi > 70 ? "text-red-400" : rsi < 30 ? "text-green-400" : "text-text-secondary"}`}>
                                                        {Number(rsi).toFixed(1)}
                                                    </span>
                                                ) : "-"}
                                            </td>
                                            <td className="text-center px-4 py-3">
                                                <div className="flex items-center justify-center gap-1">
                                                    <button onClick={(e) => { e.stopPropagation(); navigate(`/analysis/${ticker}`); }}
                                                        className="icon-btn" title="Run Analysis">
                                                        <span className="material-symbols-outlined text-[18px]">play_circle</span>
                                                    </button>
                                                    <button onClick={(e) => { e.stopPropagation(); navigate(`/data/${ticker}`); }}
                                                        className="icon-btn" title="Data Explorer">
                                                        <span className="material-symbols-outlined text-[18px]">query_stats</span>
                                                    </button>
                                                    <button onClick={(e) => { e.stopPropagation(); removeTicker(ticker); }}
                                                        className="icon-btn danger" title="Remove">
                                                        <span className="material-symbols-outlined text-[16px]">close</span>
                                                    </button>
                                                </div>
                                            </td>
                                        </tr>
                                        {expandedRow === ticker && (
                                            <tr><td colSpan={6}><TickerDetailPanel ticker={ticker} /></td></tr>
                                        )}
                                    </React.Fragment>
                                );
                            })}
                        </tbody>
                    </table>
                    {watchlist.length === 0 && (
                        <div className="flex flex-col items-center justify-center h-64 text-text-muted">
                            <span className="material-symbols-outlined text-5xl mb-4">add_chart</span>
                            <p className="text-lg mb-2">No tickers yet</p>
                            <p className="text-xs">Add a ticker symbol above to get started</p>
                        </div>
                    )}
                </div>
            </main>
        </div>
    );
};

// ***************************************************************
// SIDEBAR LAYOUT  Shared sidebar for inner pages
// ***************************************************************
const SidebarLayout = ({ children, active = "" }) => {
    const navigate = useNavigate();

    const NavLink = ({ to, icon, label, id }) => (
        <Link to={to}
            className={`flex items-center gap-3 px-3 py-2 rounded transition-colors ${active === id ? "bg-border-dark/50 border-l-2 border-primary" : "hover:bg-border-dark/50 border-l-2 border-transparent"
                }`}>
            <span className={`material-symbols-outlined text-[20px] ${active === id ? "text-primary" : "text-text-secondary"}`}>{icon}</span>
            <span className={`text-sm font-medium ${active === id ? "text-white" : "text-text-secondary"}`}>{label}</span>
        </Link>
    );

    return (
        <div className="flex h-full w-full bg-onyx-black text-gray-200 font-display">
            <aside className="w-64 bg-onyx-panel border-r border-border-dark flex flex-col shrink-0 h-full">
                <div className="h-16 flex items-center px-4 border-b border-border-dark cursor-pointer" onClick={() => navigate("/")}>
                    <div className="flex items-center gap-2">
                        <div className="w-8 h-8 bg-primary/20 rounded flex items-center justify-center">
                            <span className="material-symbols-outlined text-primary text-xl">smart_toy</span>
                        </div>
                        <div>
                            <h1 className="text-white text-base font-bold leading-none tracking-tight">LAZY BOT</h1>
                            <p className="text-text-secondary text-[10px] font-mono mt-1">v1.0  Terminal</p>
                        </div>
                    </div>
                </div>
                <div className="flex-1 overflow-y-auto py-4 px-2 flex flex-col gap-1">
                    <h3 className="px-2 text-xs font-mono text-text-muted uppercase tracking-wider mb-2">Navigation</h3>
                    <NavLink to="/" icon="monitoring" label="Watchlist" id="watchlist" />
                    <NavLink to="/settings" icon="tune" label="Settings" id="settings" />
                    <NavLink to="/diagnostics" icon="bug_report" label="Diagnostics" id="diagnostics" />
                </div>
            </aside>
            <main className="flex-1 flex flex-col overflow-hidden">{children}</main>
        </div>
    );
};

// ***************************************************************
// REUSABLE  Confidence Gauge (radial)
// ***************************************************************

const ConfidenceGauge = ({ value, size = 64 }) => {
    const pct = Math.round((value || 0) * 100);
    const r = (size - 8) / 2;
    const circ = 2 * Math.PI * r;
    const offset = circ - (pct / 100) * circ;
    const color = pct >= 70 ? "#22c55e" : pct >= 40 ? "#f59e0b" : "#ef4444";
    return (
        <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
            <svg width={size} height={size} className="-rotate-90">
                <circle cx={size / 2} cy={size / 2} r={r} stroke="rgba(40,57,50,0.4)" strokeWidth={4} fill="none" />
                <circle cx={size / 2} cy={size / 2} r={r} stroke={color} strokeWidth={4} fill="none"
                    strokeDasharray={circ} strokeDashoffset={offset} strokeLinecap="round"
                    style={{ transition: "stroke-dashoffset 0.6s ease" }} />
            </svg>
            <span className="absolute text-xs font-bold font-mono" style={{ color }}>{pct}%</span>
        </div>
    );
};

// ***************************************************************
// REUSABLE  Rating Badge
// ***************************************************************

const RatingBadge = ({ value, colorMap }) => {
    const colors = colorMap || {
        "BUY": "bg-green-500/20 text-green-400", "BULLISH": "bg-green-500/20 text-green-400",
        "STRONG_UPTREND": "bg-green-500/20 text-green-400", "UPTREND": "bg-green-500/20 text-green-400",
        "STRONG": "bg-green-500/20 text-green-400", "UNDERVALUED": "bg-green-500/20 text-green-400",
        "VERY_BULLISH": "bg-green-500/20 text-green-400", "ACCELERATING": "bg-green-500/20 text-green-400",
        "LOW": "bg-green-500/20 text-green-400", "LOW_RISK": "bg-green-500/20 text-green-400",
        "HOLD": "bg-yellow-500/20 text-yellow-400", "NEUTRAL": "bg-yellow-500/20 text-yellow-400",
        "SIDEWAYS": "bg-yellow-500/20 text-yellow-400", "FAIR": "bg-yellow-500/20 text-yellow-400",
        "MODERATE": "bg-yellow-500/20 text-yellow-400", "MODERATE_RISK": "bg-yellow-500/20 text-yellow-400",
        "STEADY": "bg-blue-500/20 text-blue-400",
        "SELL": "bg-red-500/20 text-red-400", "BEARISH": "bg-red-500/20 text-red-400",
        "DOWNTREND": "bg-red-500/20 text-red-400", "STRONG_DOWNTREND": "bg-red-500/20 text-red-400",
        "OVERVALUED": "bg-red-500/20 text-red-400", "VERY_BEARISH": "bg-red-500/20 text-red-400",
        "WEAK": "bg-red-500/20 text-red-400", "DECLINING": "bg-red-500/20 text-red-400",
        "HIGH": "bg-orange-500/20 text-orange-400", "EXTREME": "bg-red-500/20 text-red-400",
        "HIGH_RISK": "bg-orange-500/20 text-orange-400", "DO_NOT_TRADE": "bg-red-500/20 text-red-400",
        "DECELERATING": "bg-orange-500/20 text-orange-400",
    };
    const cls = colors[value] || "bg-gray-500/20 text-gray-400";
    return <span className={`px-2.5 py-0.5 rounded text-[11px] font-mono font-bold uppercase ${cls}`}>{value || "N/A"}</span>;
};

// ***************************************************************
// REUSABLE  Bullet List (green or red tinted)
// ***************************************************************

const BulletList = ({ items, color = "primary" }) => {
    if (!items || items.length === 0) return null;
    const dotColor = color === "red" ? "text-red-400" : color === "green" ? "text-green-400" : "text-primary";
    return (
        <ul className="space-y-1.5">
            {items.map((item, i) => (
                <li key={i} className="flex items-start gap-2 text-xs text-text-secondary">
                    <span className={`${dotColor} mt-0.5 shrink-0`}></span>
                    <span>{typeof item === "string" ? item : JSON.stringify(item)}</span>
                </li>
            ))}
        </ul>
    );
};

// ***************************************************************
// TECHNICAL PANEL
// ***************************************************************

const TechnicalPanel = ({ report, ticker }) => {
    if (!report) return <div className="text-text-muted text-xs p-4">Technical agent did not run or failed</div>;
    return (
        <div className="space-y-4 animate-fadeIn">
            {/* Header Stats */}
            <div className="grid grid-cols-4 gap-3">
                <div className="glass-card p-3 text-center">
                    <div className="text-[10px] text-text-muted uppercase mb-1">Trend</div>
                    <RatingBadge value={report.trend} />
                </div>
                <div className="glass-card p-3 text-center">
                    <div className="text-[10px] text-text-muted uppercase mb-1">Momentum</div>
                    <RatingBadge value={report.momentum} />
                </div>
                <div className="glass-card p-3 text-center">
                    <div className="text-[10px] text-text-muted uppercase mb-1">Signal</div>
                    <RatingBadge value={report.signal} />
                </div>
                <div className="glass-card p-3 text-center">
                    <div className="text-[10px] text-text-muted uppercase mb-1">Confidence</div>
                    <ConfidenceGauge value={report.confidence} size={48} />
                </div>
            </div>

            {/* Chart Pattern */}
            {report.chart_pattern && (
                <div className="glass-card p-3 flex items-center gap-2">
                    <span className="material-symbols-outlined text-primary text-sm">pattern</span>
                    <span className="text-xs text-text-muted">Chart Pattern:</span>
                    <span className="text-xs text-white font-bold">{report.chart_pattern}</span>
                </div>
            )}

            {/* Support / Resistance Levels */}
            <div className="grid grid-cols-2 gap-3">
                <div className="glass-card p-3">
                    <h5 className="text-[10px] text-text-muted uppercase mb-2 flex items-center gap-1">
                        <span className="w-2 h-2 rounded-full bg-green-400"></span> Support Levels
                    </h5>
                    {report.support_levels?.length > 0 ? (
                        <div className="flex flex-wrap gap-1.5">
                            {report.support_levels.map((lvl, i) => (
                                <span key={i} className="px-2 py-0.5 bg-green-500/10 text-green-400 text-[11px] font-mono rounded">
                                    ${fmt.num(lvl)}
                                </span>
                            ))}
                        </div>
                    ) : <span className="text-xs text-text-muted">None detected</span>}
                </div>
                <div className="glass-card p-3">
                    <h5 className="text-[10px] text-text-muted uppercase mb-2 flex items-center gap-1">
                        <span className="w-2 h-2 rounded-full bg-red-400"></span> Resistance Levels
                    </h5>
                    {report.resistance_levels?.length > 0 ? (
                        <div className="flex flex-wrap gap-1.5">
                            {report.resistance_levels.map((lvl, i) => (
                                <span key={i} className="px-2 py-0.5 bg-red-500/10 text-red-400 text-[11px] font-mono rounded">
                                    ${fmt.num(lvl)}
                                </span>
                            ))}
                        </div>
                    ) : <span className="text-xs text-text-muted">None detected</span>}
                </div>
            </div>

            {/* Key Signals */}
            {report.key_signals?.length > 0 && (
                <div className="glass-card p-3">
                    <h5 className="text-[10px] text-text-muted uppercase mb-2">Key Signals Detected</h5>
                    <BulletList items={report.key_signals} />
                </div>
            )}

            {/* Embedded Chart */}
            <div className="glass-card p-3">
                <h5 className="text-[10px] text-text-muted uppercase mb-2 flex items-center gap-1">
                    <span className="material-symbols-outlined text-[14px]">candlestick_chart</span>
                    Price Chart  Data Source
                </h5>
                <ChartWidget symbol={ticker} height={300} />
            </div>

            {/* Reasoning */}
            <div className="glass-card p-4">
                <h5 className="text-[10px] text-text-muted uppercase mb-2">LLM Reasoning</h5>
                <p className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap">{report.reasoning}</p>
            </div>
        </div>
    );
};

// ***************************************************************
// FUNDAMENTAL PANEL
// ***************************************************************

const FundamentalPanel = ({ report }) => {
    if (!report) return <div className="text-text-muted text-xs p-4">Fundamental agent did not run or failed</div>;
    return (
        <div className="space-y-4 animate-fadeIn">
            {/* Header Badges */}
            <div className="grid grid-cols-4 gap-3">
                <div className="glass-card p-3 text-center">
                    <div className="text-[10px] text-text-muted uppercase mb-1">Valuation</div>
                    <RatingBadge value={report.valuation_grade} />
                </div>
                <div className="glass-card p-3 text-center">
                    <div className="text-[10px] text-text-muted uppercase mb-1">Health</div>
                    <RatingBadge value={report.financial_health} />
                </div>
                <div className="glass-card p-3 text-center">
                    <div className="text-[10px] text-text-muted uppercase mb-1">Growth</div>
                    <RatingBadge value={report.growth_trajectory} />
                </div>
                <div className="glass-card p-3 text-center">
                    <div className="text-[10px] text-text-muted uppercase mb-1">Confidence</div>
                    <ConfidenceGauge value={report.confidence} size={48} />
                </div>
            </div>

            {/* Intrinsic Value */}
            {report.intrinsic_value_estimate && (
                <div className="glass-card p-3 flex items-center gap-3">
                    <span className="material-symbols-outlined text-primary">price_check</span>
                    <div>
                        <div className="text-[10px] text-text-muted uppercase">Intrinsic Value Estimate</div>
                        <div className="text-white font-bold font-mono text-lg">{fmt.usd(report.intrinsic_value_estimate)}</div>
                    </div>
                </div>
            )}

            {/* Key Metrics */}
            {report.key_metrics && Object.keys(report.key_metrics).length > 0 && (
                <div className="glass-card p-3">
                    <h5 className="text-[10px] text-text-muted uppercase mb-2">Key Metrics</h5>
                    <div className="grid grid-cols-2 gap-x-6 gap-y-1">
                        {Object.entries(report.key_metrics).map(([k, v]) => (
                            <MetricRow key={k} label={k.replace(/_/g, " ")} value={v} />
                        ))}
                    </div>
                </div>
            )}

            {/* Strengths & Risks */}
            <div className="grid grid-cols-2 gap-3">
                <div className="glass-card p-3">
                    <h5 className="text-[10px] text-green-400 uppercase mb-2 flex items-center gap-1">
                        <span className="material-symbols-outlined text-[14px]">thumb_up</span> Strengths
                    </h5>
                    <BulletList items={report.strengths} color="green" />
                </div>
                <div className="glass-card p-3">
                    <h5 className="text-[10px] text-red-400 uppercase mb-2 flex items-center gap-1">
                        <span className="material-symbols-outlined text-[14px]">warning</span> Risks
                    </h5>
                    <BulletList items={report.risks} color="red" />
                </div>
            </div>

            {/* Reasoning */}
            <div className="glass-card p-4">
                <h5 className="text-[10px] text-text-muted uppercase mb-2">LLM Reasoning</h5>
                <p className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap">{report.reasoning}</p>
            </div>
        </div>
    );
};

// ***************************************************************
// SENTIMENT PANEL
// ***************************************************************

const SentimentPanel = ({ report }) => {
    if (!report) return <div className="text-text-muted text-xs p-4">Sentiment agent did not run or failed</div>;

    // Sentiment score  color gradient
    const score = report.sentiment_score || 0;
    const barPct = ((score + 1) / 2) * 100; // -1..1  0..100
    const barColor = score > 0.3 ? "#22c55e" : score > -0.3 ? "#f59e0b" : "#ef4444";

    return (
        <div className="space-y-4 animate-fadeIn">
            {/* Sentiment Header */}
            <div className="grid grid-cols-3 gap-3">
                <div className="glass-card p-3 text-center">
                    <div className="text-[10px] text-text-muted uppercase mb-1">Overall</div>
                    <RatingBadge value={report.overall_sentiment} />
                </div>
                <div className="glass-card p-3 text-center">
                    <div className="text-[10px] text-text-muted uppercase mb-1">Score</div>
                    <div className="font-bold font-mono text-lg" style={{ color: barColor }}>{score.toFixed(2)}</div>
                    <div className="w-full h-1.5 bg-onyx-surface rounded-full mt-1.5">
                        <div className="h-full rounded-full transition-all duration-500"
                            style={{ width: `${barPct}%`, background: `linear-gradient(90deg, #ef4444, #f59e0b, #22c55e)` }} />
                    </div>
                </div>
                <div className="glass-card p-3 text-center">
                    <div className="text-[10px] text-text-muted uppercase mb-1">Signal</div>
                    <RatingBadge value={report.signal} />
                    <div className="mt-1"><ConfidenceGauge value={report.confidence} size={40} /></div>
                </div>
            </div>

            {/* Narrative Shift */}
            {report.narrative_shift && (
                <div className="glass-card p-3 border-l-2 border-primary">
                    <h5 className="text-[10px] text-primary uppercase mb-1 flex items-center gap-1">
                        <span className="material-symbols-outlined text-[14px]">trending_flat</span>
                        Narrative Shift Detected
                    </h5>
                    <p className="text-xs text-text-secondary">{report.narrative_shift}</p>
                </div>
            )}

            {/* Catalysts & Risks */}
            <div className="grid grid-cols-2 gap-3">
                <div className="glass-card p-3">
                    <h5 className="text-[10px] text-green-400 uppercase mb-2 flex items-center gap-1">
                        <span className="material-symbols-outlined text-[14px]">rocket_launch</span> Catalysts
                    </h5>
                    <BulletList items={report.catalysts} color="green" />
                </div>
                <div className="glass-card p-3">
                    <h5 className="text-[10px] text-red-400 uppercase mb-2 flex items-center gap-1">
                        <span className="material-symbols-outlined text-[14px]">warning</span> Risks Mentioned
                    </h5>
                    <BulletList items={report.risks_mentioned} color="red" />
                </div>
            </div>

            {/* Top Headlines */}
            {report.top_headlines?.length > 0 && (
                <div className="glass-card p-3">
                    <h5 className="text-[10px] text-text-muted uppercase mb-2">Top Headlines (LLM-Selected)</h5>
                    <div className="space-y-1.5">
                        {report.top_headlines.map((h, i) => (
                            <div key={i} className="flex items-baseline gap-2 text-xs">
                                <span className="text-primary shrink-0"></span>
                                <span className="text-white">{h.title || h.headline || JSON.stringify(h)}</span>
                                {h.source && <span className="text-text-muted text-[10px] shrink-0"> {h.source}</span>}
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Reasoning */}
            <div className="glass-card p-4">
                <h5 className="text-[10px] text-text-muted uppercase mb-2">LLM Reasoning</h5>
                <p className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap">{report.reasoning}</p>
            </div>
        </div>
    );
};

// ***************************************************************
// RISK PANEL
// ***************************************************************

const RiskPanel = ({ report }) => {
    if (!report) return <div className="text-text-muted text-xs p-4">Risk agent did not run or failed</div>;
    return (
        <div className="space-y-4 animate-fadeIn">
            {/* Risk Header */}
            <div className="grid grid-cols-3 gap-3">
                <div className="glass-card p-3 text-center">
                    <div className="text-[10px] text-text-muted uppercase mb-1">Risk Grade</div>
                    <RatingBadge value={report.risk_grade} />
                </div>
                <div className="glass-card p-3 text-center">
                    <div className="text-[10px] text-text-muted uppercase mb-1">Volatility</div>
                    <RatingBadge value={report.volatility_rating} />
                </div>
                <div className="glass-card p-3 text-center">
                    <div className="text-[10px] text-text-muted uppercase mb-1">R/R Ratio</div>
                    <div className="text-lg font-bold font-mono text-white">{fmt.num(report.risk_reward_ratio, 2)}</div>
                </div>
            </div>

            {/* Position Sizing */}
            <div className="glass-card p-4">
                <h5 className="text-[10px] text-text-muted uppercase mb-3 flex items-center gap-1">
                    <span className="material-symbols-outlined text-[14px]">account_balance</span>
                    Position Sizing
                </h5>
                <div className="grid grid-cols-4 gap-4">
                    <div className="text-center">
                        <div className="text-[10px] text-text-muted uppercase mb-1">Max Position</div>
                        <div className="text-white font-bold font-mono">{fmt.pctRaw(report.max_position_size_pct / 100)}</div>
                        <div className="text-[9px] text-text-muted mt-0.5">of portfolio</div>
                    </div>
                    <div className="text-center">
                        <div className="text-[10px] text-text-muted uppercase mb-1">Stop Loss Offset</div>
                        <div className="text-red-400 font-bold font-mono">{fmt.usd(Math.abs(report.suggested_stop_loss))}</div>
                        <div className="text-[9px] text-red-400/60 mt-0.5">below entry price</div>
                    </div>
                    <div className="text-center">
                        <div className="text-[10px] text-text-muted uppercase mb-1">Take Profit Target</div>
                        <div className="text-green-400 font-bold font-mono">+{fmt.usd(report.suggested_take_profit)}</div>
                        <div className="text-[9px] text-green-400/60 mt-0.5">above entry price</div>
                    </div>
                    <div className="text-center">
                        <div className="text-[10px] text-text-muted uppercase mb-1">ATR Stop Distance</div>
                        <div className="text-yellow-400 font-bold font-mono">{fmt.usd(report.atr_based_stop)}</div>
                        <div className="text-[9px] text-yellow-400/60 mt-0.5">volatility-based</div>
                    </div>
                </div>
            </div>

            {/* Downside Scenarios */}
            {report.downside_scenarios?.length > 0 && (
                <div className="glass-card p-3">
                    <h5 className="text-[10px] text-red-400 uppercase mb-2 flex items-center gap-1">
                        <span className="material-symbols-outlined text-[14px]">trending_down</span>
                        Downside Scenarios
                    </h5>
                    <BulletList items={report.downside_scenarios} color="red" />
                </div>
            )}

            {/* Portfolio Impact */}
            {report.portfolio_impact && (
                <div className="glass-card p-3">
                    <h5 className="text-[10px] text-text-muted uppercase mb-2">Portfolio Impact</h5>
                    <p className="text-xs text-text-secondary">{report.portfolio_impact}</p>
                </div>
            )}

            {/* Reasoning */}
            <div className="glass-card p-4">
                <h5 className="text-[10px] text-text-muted uppercase mb-2">LLM Reasoning</h5>
                <p className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap">{report.reasoning}</p>
            </div>
        </div>
    );
};

// ***************************************************************
// DECISION SECTION  Rule evaluations + position sizing
// ***************************************************************

const DecisionSection = ({ decision }) => {
    if (!decision) return null;
    return (
        <div className="space-y-4 animate-fadeIn">
            {/* Decision Banner */}
            <div className={`decision-banner ${decision.signal?.toLowerCase()} bg-onyx-panel`}>
                <div className="flex items-center justify-between relative z-10">
                    <div className="flex items-center gap-4">
                        <span className={`text-4xl font-bold font-mono ${signalColor(decision.signal)}`}>
                            {decision.signal}
                        </span>
                        <div className="border-l border-border-dark pl-4">
                            <div className="text-xs text-text-muted uppercase">Confidence</div>
                            <div className="text-white text-lg font-bold font-mono">
                                {fmt.pctRaw(decision.confidence)}
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Position Sizing */}
            <div className="glass-card p-4">
                <h5 className="text-[10px] text-text-muted uppercase mb-3">Position Recommendations</h5>
                {(() => {
                    const entry = decision.suggested_entry_price || 0;
                    const slOffset = Math.abs(decision.suggested_stop_loss || 0);
                    const tpOffset = decision.suggested_take_profit || 0;
                    const slPrice = entry > 0 && slOffset > 0 ? entry - slOffset : null;
                    const tpPrice = entry > 0 && tpOffset > 0 ? entry + tpOffset : null;
                    return (
                        <div className="grid grid-cols-5 gap-3">
                            <div className="text-center">
                                <div className="text-[10px] text-text-muted uppercase mb-1">Position Size</div>
                                <div className="text-sm text-white font-bold font-mono">{fmt.pctRaw(decision.suggested_position_size_pct / 100)}</div>
                                <div className="text-[9px] text-text-muted mt-0.5">of portfolio</div>
                            </div>
                            <div className="text-center">
                                <div className="text-[10px] text-text-muted uppercase mb-1">Entry Price</div>
                                <div className="text-sm text-white font-bold font-mono">{fmt.usd(entry)}</div>
                            </div>
                            <div className="text-center">
                                <div className="text-[10px] text-text-muted uppercase mb-1">Stop Loss</div>
                                <div className="text-sm text-red-400 font-bold font-mono">{slPrice ? fmt.usd(slPrice) : "N/A"}</div>
                                <div className="text-[9px] text-red-400/60 mt-0.5">{slOffset > 0 ? `-${fmt.usd(slOffset)} from entry` : ""}</div>
                            </div>
                            <div className="text-center">
                                <div className="text-[10px] text-text-muted uppercase mb-1">Take Profit</div>
                                <div className="text-sm text-green-400 font-bold font-mono">{tpPrice ? fmt.usd(tpPrice) : "N/A"}</div>
                                <div className="text-[9px] text-green-400/60 mt-0.5">{tpOffset > 0 ? `+${fmt.usd(tpOffset)} from entry` : ""}</div>
                            </div>
                            <div className="text-center">
                                <div className="text-[10px] text-text-muted uppercase mb-1">R/R Ratio</div>
                                <div className="text-sm text-white font-bold font-mono">{fmt.num(decision.risk_reward_ratio, 2)}</div>
                                <div className="text-[9px] text-text-muted mt-0.5">risk to reward</div>
                            </div>
                        </div>
                    );
                })()}
            </div>

            {/* Rule Evaluations */}
            {(decision.entry_rules_evaluated?.length > 0 || decision.exit_rules_evaluated?.length > 0) && (
                <div className="glass-card p-4">
                    <h5 className="text-[10px] text-text-muted uppercase mb-3 flex items-center gap-1">
                        <span className="material-symbols-outlined text-[14px]">checklist</span>
                        Rule-by-Rule Evaluation
                    </h5>
                    {decision.entry_rules_evaluated?.length > 0 && (
                        <div className="mb-3">
                            <div className="text-[10px] text-primary uppercase mb-2 font-bold">Entry Rules</div>
                            {decision.entry_rules_evaluated.map((rule, i) => (
                                <div key={i} className="flex items-start gap-2 py-1.5 border-b border-border-dark/50 last:border-0">
                                    <span className={`text-sm shrink-0 ${rule.is_met ? "text-green-400" : "text-red-400"}`}>
                                        {rule.is_met ? "" : "--"}
                                    </span>
                                    <div className="flex-1">
                                        <div className="text-xs text-white">{rule.rule_text}</div>
                                        <div className="text-[10px] text-text-muted mt-0.5">{rule.evidence}</div>
                                    </div>
                                    <span className="text-[10px] text-text-muted font-mono shrink-0">{rule.data_source}</span>
                                </div>
                            ))}
                        </div>
                    )}
                    {decision.exit_rules_evaluated?.length > 0 && (
                        <div>
                            <div className="text-[10px] text-yellow-400 uppercase mb-2 font-bold">Exit Rules</div>
                            {decision.exit_rules_evaluated.map((rule, i) => (
                                <div key={i} className="flex items-start gap-2 py-1.5 border-b border-border-dark/50 last:border-0">
                                    <span className={`text-sm shrink-0 ${rule.is_met ? "text-green-400" : "text-red-400"}`}>
                                        {rule.is_met ? "" : "--"}
                                    </span>
                                    <div className="flex-1">
                                        <div className="text-xs text-white">{rule.rule_text}</div>
                                        <div className="text-[10px] text-text-muted mt-0.5">{rule.evidence}</div>
                                    </div>
                                    <span className="text-[10px] text-text-muted font-mono shrink-0">{rule.data_source}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {/* Dissenting Signals */}
            {decision.dissenting_signals?.length > 0 && (
                <div className="glass-card p-3 border-l-2 border-yellow-500">
                    <h5 className="text-[10px] text-yellow-400 uppercase mb-2 flex items-center gap-1">
                        <span className="material-symbols-outlined text-[14px]">warning</span>
                        Dissenting Signals
                    </h5>
                    <BulletList items={decision.dissenting_signals} color="red" />
                </div>
            )}

            {/* Full Reasoning */}
            {decision.reasoning && (
                <div className="glass-card p-4">
                    <h5 className="text-[10px] text-text-muted uppercase mb-2">Final Decision Reasoning</h5>
                    <p className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap">{decision.reasoning}</p>
                </div>
            )}
        </div>
    );
};

// ***************************************************************
// STEP TRACKER — real-time pipeline progress bar
// ***************************************************************

const STEP_LABELS = {
    price_history: "Prices",
    fundamentals: "Fundamentals",
    financial_history: "Financials",
    balance_sheet: "Balance Sheet",
    cashflow: "Cash Flow",
    analyst_data: "Analyst",
    insider_activity: "Insider",
    earnings_calendar: "Earnings",
    technicals: "Technicals",
    risk_metrics: "Risk Metrics",
    news_scrape: "News Scrape",
    news: "News Load",
    youtube_scrape: "YouTube Scrape",
    youtube: "YouTube Load",
};

const StepTracker = ({ steps, phase }) => {
    const entries = Object.entries(steps);
    if (entries.length === 0) return null;

    const total = entries.length;
    const completed = entries.filter(([, v]) => v.status === "ok" || v.status === "error").length;
    const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

    return (
        <div className="px-6 py-4 bg-onyx-surface border-b border-border-dark shrink-0">
            <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                    <span className="material-symbols-outlined text-primary text-[16px]">database</span>
                    <span className="text-xs text-text-secondary font-mono uppercase">Data Collection</span>
                </div>
                <span className="text-xs font-mono text-text-muted">{completed}/{total} steps · {pct}%</span>
            </div>
            <div className="w-full h-1.5 bg-onyx-black rounded-full mb-3 overflow-hidden">
                <div className="h-full rounded-full transition-all duration-500 ease-out"
                    style={{ width: `${pct}%`, background: "linear-gradient(90deg, #13ec99, #137fec)" }} />
            </div>
            <div className="flex flex-wrap gap-2">
                {entries.map(([name, info]) => {
                    let dotClass = "step-dot-pending";
                    let icon = "radio_button_unchecked";
                    if (info.status === "running") { dotClass = "step-dot-running"; icon = "progress_activity"; }
                    else if (info.status === "ok") { dotClass = "step-dot-ok"; icon = "check_circle"; }
                    else if (info.status === "error") { dotClass = "step-dot-error"; icon = "error"; }
                    return (
                        <div key={name} className={`step-dot ${dotClass}`} title={name}>
                            <span className={`material-symbols-outlined text-[12px] ${info.status === "running" ? "animate-spin" : ""}`}>{icon}</span>
                            <span className="text-[10px] font-mono">{STEP_LABELS[name] || name}</span>
                        </div>
                    );
                })}
            </div>
        </div>
    );
};

// ***************************************************************
// AGENT SKELETON — shimmer placeholder while agent is processing
// ***************************************************************

const AgentSkeleton = ({ name }) => (
    <div className="space-y-4 animate-fadeIn">
        <div className="flex items-center gap-3 mb-4">
            <span className="material-symbols-outlined text-primary text-2xl animate-spin">progress_activity</span>
            <div>
                <div className="text-sm text-white font-bold capitalize">{name} Agent</div>
                <div className="text-[10px] text-text-muted font-mono">Analyzing data with LLM…</div>
            </div>
        </div>
        <div className="space-y-3">
            {[1, 2, 3].map(i => (
                <div key={i} className="glass-card p-4">
                    <div className="shimmer-line h-3 w-1/3 mb-3 rounded" />
                    <div className="shimmer-line h-2 w-full mb-2 rounded" />
                    <div className="shimmer-line h-2 w-4/5 mb-2 rounded" />
                    <div className="shimmer-line h-2 w-2/3 rounded" />
                </div>
            ))}
        </div>
    </div>
);

// ***************************************************************
// ANALYSIS PAGE — Progressive rendering with SSE streaming
// ***************************************************************

const AnalysisPage = ({
    runAnalysis, analyzing, analysisResult,
    runAnalysisStream, streamSteps, streamAgents,
    streamDecision, streamErrors, streamPlan, streamPhase,
    loadCachedAnalysis, cachedDate,
}) => {
    const { ticker } = useParams();
    const navigate = useNavigate();
    const [started, setStarted] = useState(false);
    const [mode, setMode] = useState("full");
    const [activeAgent, setActiveAgent] = useState("data");
    const [cacheLoaded, setCacheLoaded] = useState(false);
    const cacheAttempted = useRef(false);

    // Auto-load cached analysis on mount
    useEffect(() => {
        if (cacheAttempted.current) return;
        cacheAttempted.current = true;
        (async () => {
            const hadCache = await loadCachedAnalysis(ticker);
            if (hadCache) {
                setCacheLoaded(true);
                setStarted(true); // Show the agent panels
                setActiveAgent("decision"); // Jump to decision
            }
        })();
    }, [ticker, loadCachedAnalysis]);

    // Automatically switch from Data to Decision ONLY when everything is done
    useEffect(() => {
        if (streamPhase === "done" && activeAgent === "data") {
            // Optional: Auto-switch to Decision when analysis completes
            // setActiveAgent("decision");
        }
    }, [streamPhase]);

    const handleStart = async () => {
        setCacheLoaded(false); // Clear cached indicator when re-running
        setStarted(true);
        await runAnalysisStream(ticker, mode);
    };

    // Build reports from streaming agents
    const reports = {};
    Object.entries(streamAgents).forEach(([name, info]) => {
        if (info.status === "ok" && info.report) reports[name] = info.report;
    });
    const decision = streamDecision || {};
    const hasAnyData = started || Object.keys(streamSteps).length > 0 || Object.keys(streamAgents).length > 0;

    const agentTabs = [
        { id: "decision", icon: "gavel", label: "Decision", color: "#13ec99" },
        { id: "technical", icon: "show_chart", label: "Technical", color: "#3b82f6" },
        { id: "fundamental", icon: "account_balance", label: "Fundamental", color: "#10b981" },
        { id: "sentiment", icon: "mood", label: "Sentiment", color: "#f59e0b" },
        { id: "risk", icon: "shield", label: "Risk", color: "#ef4444" },
    ];

    return (
        <SidebarLayout active="">
            {/* Top Bar */}
            <div className="h-14 flex items-center justify-between px-6 border-b border-border-dark bg-onyx-panel shrink-0">
                <div className="flex items-center gap-3">
                    <button onClick={() => navigate("/")} className="icon-btn">
                        <span className="material-symbols-outlined text-xl">arrow_back</span>
                    </button>
                    <h2 className="text-white font-bold text-lg">Analysis: <span className="text-primary">{ticker}</span></h2>
                    {cacheLoaded && cachedDate && (
                        <span className="text-[10px] bg-emerald-500/10 text-emerald-400 px-2 py-0.5 rounded-full font-mono">
                            Last analyzed: {cachedDate}
                        </span>
                    )}
                </div>
                <div className="flex items-center gap-2">
                    <select value={mode} onChange={e => setMode(e.target.value)}
                        className="bg-onyx-black border border-border-dark rounded px-3 py-1.5 text-xs text-white font-mono focus:outline-none">
                        <option value="full">Full Analysis</option>
                        <option value="quick">Quick (Price + Tech)</option>
                        <option value="news">News + Sentiment</option>
                        <option value="data">Data Collection Only</option>
                    </select>
                    <button onClick={handleStart} disabled={analyzing}
                        className={`px-4 py-1.5 text-xs font-bold rounded transition flex items-center gap-2 ${analyzing ? "bg-primary/10 text-primary/50 cursor-wait" : cacheLoaded ? "bg-amber-500/20 hover:bg-amber-500/30 text-amber-400" : "bg-primary/20 hover:bg-primary/30 text-primary"}`}>
                        <span className="material-symbols-outlined text-[16px]">{analyzing ? "progress_activity" : cacheLoaded ? "refresh" : "play_arrow"}</span>
                        {analyzing ? "Analyzing…" : cacheLoaded ? "Re-Analyze" : "Run Analysis"}
                    </button>
                </div>
            </div>

            <div className="flex-1 overflow-hidden flex flex-col">
                {/* Pre-run state */}
                {!hasAnyData && (
                    <div className="flex-1 flex flex-col items-center justify-center text-text-muted">
                        <span className="material-symbols-outlined text-6xl mb-4">psychology</span>
                        <p className="text-lg mb-2">Ready to analyze <span className="text-primary font-bold">{ticker}</span></p>
                        <p className="text-xs mb-6">Click "Run Analysis" to start the AI pipeline</p>
                        <button onClick={handleStart}
                            className="px-6 py-3 bg-primary/20 hover:bg-primary/30 text-primary font-bold rounded-lg transition animate-pulse-glow">
                            Start Full Analysis
                        </button>
                    </div>
                )}

                {/* Streaming state — progressive rendering */}
                {hasAnyData && (
                    <>
                        {/* Error Banner */}
                        {streamErrors.length > 0 && (
                            <div className="bg-red-500/10 border-l-4 border-red-500 p-4 mx-6 mt-4 mb-2">
                                <h4 className="text-red-400 font-bold text-sm mb-2 flex items-center gap-2">
                                    <span className="material-symbols-outlined text-base">error</span>
                                    Pipeline Errors
                                </h4>
                                <ul className="list-disc list-inside text-xs text-red-200/80 space-y-1">
                                    {streamErrors.map((err, i) => <li key={i}>{err}</li>)}
                                </ul>
                            </div>
                        )}

                        {/* Step Tracker — only visible during/after live analysis (not cached) */}
                        {Object.keys(streamSteps).length > 0 && <StepTracker steps={streamSteps} phase={streamPhase} />}

                        {/* Agent Phase: show tab bar + panels */}
                        {/* Agent Phase: show tab bar + panels */}
                        <div className="flex border-b border-border-dark px-6 bg-onyx-surface shrink-0">
                            {/* Always show Data tab */}
                            <button onClick={() => setActiveAgent("data")}
                                className={`tab-btn flex items-center gap-1.5 ${activeAgent === "data" ? "active" : ""}`}>
                                <span className="material-symbols-outlined text-sm">database</span>
                                Data Explorer
                            </button>

                            {(streamPhase === "agents" || streamPhase === "decision" || streamPhase === "done") && agentTabs.map(tab => {
                                const agentInfo = streamAgents[tab.id];
                                const report = tab.id === "decision" ? decision : reports[tab.id];
                                const hasData = report && Object.keys(report).length > 0;
                                const isRunning = agentInfo?.status === "running";
                                const isFailed = agentInfo?.status === "error";
                                const signal = tab.id === "decision" ? decision?.signal : report?.signal;
                                return (
                                    <button key={tab.id} onClick={() => setActiveAgent(tab.id)}
                                        className={`tab-btn flex items-center gap-1.5 ${activeAgent === tab.id ? "active" : ""}`}>
                                        <span className="material-symbols-outlined text-sm"
                                            style={activeAgent === tab.id ? { color: tab.color } : {}}>
                                            {tab.icon}
                                        </span>
                                        {tab.label}
                                        {isRunning && <span className="material-symbols-outlined text-[12px] text-primary animate-spin">progress_activity</span>}
                                        {signal && <span className={`ml-1 text-[10px] font-mono font-bold ${signalColor(signal)}`}>{signal}</span>}
                                        {isFailed && <span className="text-[10px] text-red-400">✗</span>}
                                        {!hasData && !isRunning && !isFailed && tab.id !== "decision" && <span className="text-[10px] text-text-muted">⏳</span>}
                                    </button>
                                );
                            })}
                        </div>

                        <div className="flex-1 overflow-y-auto p-0 bg-onyx-black layout-content">
                            {activeAgent === "data" && (
                                <TickerDetailPanel
                                    ticker={ticker}
                                    streamSignals={
                                        // Reduce streamSteps to a simple status map
                                        Object.fromEntries(
                                            Object.entries(streamSteps).map(([k, v]) => [k, v.status])
                                        )
                                    }
                                />
                            )}
                            <div className="p-6">
                                {activeAgent === "decision" && (streamDecision ? <DecisionSection decision={decision} /> : <AgentSkeleton name="decision" />)}
                                {activeAgent === "technical" && (reports.technical ? <TechnicalPanel report={reports.technical} ticker={ticker} /> : streamAgents.technical?.status === "error" ? <div className="text-red-400 text-xs p-4">Technical agent failed: {streamAgents.technical.error}</div> : <AgentSkeleton name="technical" />)}
                                {activeAgent === "fundamental" && (reports.fundamental ? <FundamentalPanel report={reports.fundamental} /> : streamAgents.fundamental?.status === "error" ? <div className="text-red-400 text-xs p-4">Fundamental agent failed: {streamAgents.fundamental.error}</div> : <AgentSkeleton name="fundamental" />)}
                                {activeAgent === "sentiment" && (reports.sentiment ? <SentimentPanel report={reports.sentiment} /> : streamAgents.sentiment?.status === "error" ? <div className="text-red-400 text-xs p-4">Sentiment agent failed: {streamAgents.sentiment.error}</div> : <AgentSkeleton name="sentiment" />)}
                                {activeAgent === "risk" && (reports.risk ? <RiskPanel report={reports.risk} /> : streamAgents.risk?.status === "error" ? <div className="text-red-400 text-xs p-4">Risk agent failed: {streamAgents.risk.error}</div> : <AgentSkeleton name="risk" />)}
                            </div>
                        </div>

                        {/* Data-only phase: No longer blocks! Data is shown via activeAgent="data" */}
                    </>
                )}

            </div>
        </SidebarLayout>
    );
};


// ***************************************************************
// YOUTUBE TAB — Collapsible transcript cards
// ***************************************************************

const YouTubeTab = ({ videos }) => {
    const [expanded, setExpanded] = useState(null); // video_id or null

    const toggle = (videoId, e) => {
        e.preventDefault();
        e.stopPropagation();
        setExpanded(prev => prev === videoId ? null : videoId);
    };

    const formatDuration = (secs) => {
        if (!secs) return "";
        const m = Math.floor(secs / 60);
        const s = secs % 60;
        return `${m}:${String(s).padStart(2, "0")}`;
    };

    if (videos.length === 0) {
        return React.createElement("div", { className: "text-center py-12 text-text-muted" }, "No YouTube transcripts in database");
    }

    return (
        <div className="space-y-3">
            {videos.map((v) => {
                const isOpen = expanded === v.video_id;
                return (
                    <div key={v.video_id} className="glass-card overflow-hidden transition-all duration-200"
                        style={{ borderColor: isOpen ? "var(--color-primary)" : undefined, borderWidth: isOpen ? "1px" : undefined }}>
                        {/* Header row */}
                        <div className="flex items-center gap-3 p-4 cursor-pointer hover:bg-white/[0.02] transition"
                            onClick={(e) => toggle(v.video_id, e)}>
                            {/* Play icon */}
                            <span className="material-symbols-outlined text-red-400 text-2xl shrink-0">play_circle</span>
                            {/* Video info */}
                            <div className="flex-1 min-w-0">
                                <a href={`https://youtube.com/watch?v=${v.video_id}`} target="_blank" rel="noopener"
                                    className="text-sm text-white hover:text-primary transition truncate block"
                                    onClick={(e) => e.stopPropagation()}>
                                    {v.title}
                                </a>
                                <div className="flex flex-wrap gap-3 text-[10px] text-text-muted mt-1">
                                    <span className="font-bold text-text-secondary">{v.channel}</span>
                                    <span>{fmt.date(v.published_at)}</span>
                                    {v.duration_seconds > 0 && <span>{formatDuration(v.duration_seconds)}</span>}
                                    {v.transcript_length > 0 && (
                                        <span className="text-emerald-400/70">
                                            <span className="material-symbols-outlined text-[10px] align-middle mr-0.5">description</span>
                                            {(v.transcript_length / 1000).toFixed(1)}k chars
                                        </span>
                                    )}
                                </div>
                            </div>
                            {/* Expand/collapse chevron */}
                            {v.raw_transcript && (
                                <span className={`material-symbols-outlined text-text-muted text-lg transition-transform duration-200 shrink-0 ${isOpen ? "rotate-180" : ""}`}>
                                    expand_more
                                </span>
                            )}
                        </div>
                        {/* Collapsible transcript body */}
                        {isOpen && v.raw_transcript && (
                            <div className="border-t border-border-dark">
                                <div className="p-4 max-h-[400px] overflow-y-auto"
                                    style={{ scrollbarWidth: "thin", scrollbarColor: "var(--color-border-dark) transparent" }}>
                                    <div className="flex items-center justify-between mb-3">
                                        <span className="text-[10px] uppercase tracking-widest text-text-muted font-bold">Transcript</span>
                                        <button className="text-[10px] text-primary/60 hover:text-primary transition flex items-center gap-1"
                                            onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(v.raw_transcript); }}>
                                            <span className="material-symbols-outlined text-xs">content_copy</span> Copy
                                        </button>
                                    </div>
                                    <p className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap font-mono"
                                        style={{ fontSize: "11px", lineHeight: "1.7" }}>
                                        {v.raw_transcript}
                                    </p>
                                </div>
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
};


// ***************************************************************
// DATA EXPLORER PAGE  Deep view into a ticker's data
// ***************************************************************

const DataExplorerPage = () => {
    const { ticker } = useParams();
    const navigate = useNavigate();
    const [tab, setTab] = useState("CHART");
    const [news, setNews] = useState([]);
    const [videos, setVideos] = useState([]);
    const [risk, setRisk] = useState({});
    const [analyst, setAnalyst] = useState({});

    useEffect(() => {
        const load = async () => {
            const [newsRes, ytRes, riskRes, analystRes] = await Promise.all([
                fetch(`/api/dashboard/news/${ticker}`),
                fetch(`/api/dashboard/youtube/${ticker}`),
                fetch(`/api/dashboard/risk/${ticker}`),
                fetch(`/api/dashboard/analyst/${ticker}`),
            ]);
            const newsJson = await newsRes.json(); setNews(newsJson.articles || []);
            const ytJson = await ytRes.json(); setVideos(ytJson.videos || []);
            const riskJson = await riskRes.json(); setRisk(riskJson.metrics || {});
            setAnalyst(await analystRes.json());
        };
        load();
    }, [ticker]);

    const TabBtn = ({ id, label, icon }) => (
        <button onClick={() => setTab(id)}
            className={`tab-btn flex items-center gap-1.5 ${tab === id ? "active" : ""}`}>
            <span className="material-symbols-outlined text-sm">{icon}</span>{label}
        </button>
    );

    return (
        <SidebarLayout active="">
            <div className="h-14 flex items-center justify-between px-6 border-b border-border-dark bg-onyx-panel shrink-0">
                <div className="flex items-center gap-3">
                    <button onClick={() => navigate("/")} className="icon-btn">
                        <span className="material-symbols-outlined text-xl">arrow_back</span>
                    </button>
                    <h2 className="text-white font-bold text-lg">Data Explorer: <span className="text-primary">{ticker}</span></h2>
                </div>
            </div>
            <div className="flex border-b border-border-dark px-6 bg-onyx-surface">
                <TabBtn id="CHART" label="Chart" icon="candlestick_chart" />
                <TabBtn id="NEWS" label="News" icon="newspaper" />
                <TabBtn id="YT" label="YouTube" icon="play_circle" />
                <TabBtn id="RISK" label="Risk" icon="shield" />
                <TabBtn id="ANALYST" label="Analyst" icon="groups" />
            </div>
            <div className="flex-1 overflow-y-auto p-6">
                {tab === "CHART" && <ChartWidget symbol={ticker} height={500} />}
                {tab === "NEWS" && (
                    <div className="space-y-2">
                        {news.map((item, i) => (
                            <a key={i} href={item.url} target="_blank" rel="noopener"
                                className="block glass-card p-4 hover:border-primary/30 transition group">
                                <h5 className="text-sm text-white group-hover:text-primary mb-1">{item.title}</h5>
                                <div className="flex gap-3 text-[10px] text-text-muted">
                                    <span className="font-bold text-text-secondary">{item.publisher || item.source}</span>
                                    <span>{fmt.date(item.published_at)}</span>
                                </div>
                                {item.summary && <p className="text-xs text-text-muted mt-2 line-clamp-2">{item.summary}</p>}
                            </a>
                        ))}
                        {news.length === 0 && <div className="text-center py-12 text-text-muted">No news articles in database</div>}
                    </div>
                )}
                {tab === "YT" && (
                    <YouTubeTab videos={videos} />
                )}
                {tab === "RISK" && (
                    <div className="grid grid-cols-3 gap-4">
                        {Object.entries(risk).filter(([k]) => k !== "ticker" && k !== "computed_date").map(([key, val]) => (
                            <div key={key} className="glass-card p-4 text-center">
                                <div className="text-xl font-bold font-mono text-white mb-1">{fmt.num(val, 4)}</div>
                                <div className="text-[10px] text-text-muted uppercase">{key.replace(/_/g, " ")}</div>
                            </div>
                        ))}
                        {Object.keys(risk).length === 0 && <div className="col-span-3 text-center py-12 text-text-muted">No risk metrics in database</div>}
                    </div>
                )}
                {tab === "ANALYST" && (
                    <div className="grid grid-cols-2 gap-6">
                        <div className="glass-card p-4">
                            <h4 className="text-xs text-text-muted uppercase mb-3">Analyst Targets</h4>
                            {analyst.analyst && Object.entries(analyst.analyst).filter(([k]) => !["ticker", "snapshot_date"].includes(k)).map(([k, v]) => (
                                <MetricRow key={k} label={k.replace(/_/g, " ")} value={typeof v === "number" ? fmt.num(v) : String(v ?? "N/A")} />
                            ))}
                            {!analyst.analyst && <div className="text-text-muted text-xs">No analyst data</div>}
                        </div>
                        <div className="glass-card p-4">
                            <h4 className="text-xs text-text-muted uppercase mb-3">Insider Activity</h4>
                            {analyst.insider && Object.entries(analyst.insider).filter(([k]) => !["ticker", "snapshot_date"].includes(k)).map(([k, v]) => (
                                <MetricRow key={k} label={k.replace(/_/g, " ")} value={typeof v === "number" ? fmt.num(v) : String(v ?? "N/A")} />
                            ))}
                            {!analyst.insider && <div className="text-text-muted text-xs">No insider data</div>}
                        </div>
                    </div>
                )}
            </div>
        </SidebarLayout>
    );
};

// ***************************************************************
// SETTINGS PAGE
// ***************************************************************

const SettingsPage = () => {
    const [strategy, setStrategy] = useState("");
    const [riskParams, setRiskParams] = useState("");
    const [saveStatus, setSaveStatus] = useState(null);
    const [loading, setLoading] = useState(true);

    // LLM config state
    const [llmConfig, setLlmConfig] = useState({
        provider: "ollama",
        ollama_url: "",
        lmstudio_url: "",
        model: "",
        context_size: 8192,
        temperature: 0.3,
    });
    const [models, setModels] = useState([]);
    const [modelsFetching, setModelsFetching] = useState(false);
    const [llmConnected, setLlmConnected] = useState(null); // null = unknown, true/false

    useEffect(() => {
        const load = async () => {
            try {
                const [stratRes, riskRes, llmRes] = await Promise.all([
                    fetch("/api/strategy"),
                    fetch("/api/risk-params"),
                    fetch("/api/llm-config"),
                ]);
                const stratData = await stratRes.json();
                setStrategy(stratData.strategy || "");
                const riskData = await riskRes.json();
                setRiskParams(JSON.stringify(riskData, null, 2));
                const llmData = await llmRes.json();
                setLlmConfig(llmData);
                // Auto-fetch models on load
                fetchModels(llmData.provider, llmData.provider === "lmstudio" ? llmData.lmstudio_url : llmData.ollama_url);
            } catch (e) {
                console.error("Settings load error:", e);
            } finally {
                setLoading(false);
            }
        };
        load();
    }, []);

    const fetchModels = async (provider, url) => {
        if (!url) return;
        setModelsFetching(true);
        setLlmConnected(null);
        try {
            const params = new URLSearchParams();
            if (provider) params.set("provider", provider);
            if (url) params.set("url", url);
            const res = await fetch(`/api/llm-models?${params}`);
            const data = await res.json();
            setModels(data.models || []);
            setLlmConnected(data.connected);
        } catch (e) {
            setModels([]);
            setLlmConnected(false);
        } finally {
            setModelsFetching(false);
        }
    };

    const activeUrl = llmConfig.provider === "lmstudio" ? llmConfig.lmstudio_url : llmConfig.ollama_url;

    const setActiveUrl = (val) => {
        if (llmConfig.provider === "lmstudio") {
            setLlmConfig(prev => ({ ...prev, lmstudio_url: val }));
        } else {
            setLlmConfig(prev => ({ ...prev, ollama_url: val }));
        }
    };

    const saveLlmConfig = async () => {
        setSaveStatus("saving");
        try {
            await fetch("/api/llm-config", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(llmConfig),
            });
            setSaveStatus("saved");
        } catch (e) {
            setSaveStatus("error");
        }
        setTimeout(() => setSaveStatus(null), 2000);
    };

    const saveStrategy = async () => {
        setSaveStatus("saving");
        await fetch("/api/strategy", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ strategy_text: strategy }),
        });
        setSaveStatus("saved");
        setTimeout(() => setSaveStatus(null), 2000);
    };

    const saveRisk = async () => {
        setSaveStatus("saving");
        try {
            const parsed = JSON.parse(riskParams);
            await fetch("/api/risk-params", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ params: parsed }),
            });
            setSaveStatus("saved");
        } catch (e) {
            setSaveStatus("error");
        }
        setTimeout(() => setSaveStatus(null), 2000);
    };

    if (loading) return <SidebarLayout active="settings"><Spinner /></SidebarLayout>;

    return (
        <SidebarLayout active="settings">
            <div className="h-14 flex items-center px-6 border-b border-border-dark bg-onyx-panel shrink-0">
                <h2 className="text-white font-bold text-lg">Settings</h2>
                {saveStatus && (
                    <span className={`ml-4 text-xs font-mono ${saveStatus === "saved" ? "text-green-400" : saveStatus === "error" ? "text-red-400" : "text-primary"}`}>
                        {saveStatus === "saving" ? "Saving|" : saveStatus === "saved" ? "✓ Saved" : "✗ Error"}
                    </span>
                )}
            </div>
            <div className="flex-1 overflow-y-auto p-6 space-y-6">
                {/* ── LLM Configuration ── */}
                <div className="glass-card p-5">
                    <div className="flex justify-between items-center mb-5">
                        <h3 className="text-sm font-bold text-white flex items-center gap-2">
                            <span className="material-symbols-outlined text-primary text-[18px]">psychology</span>
                            LLM Configuration
                        </h3>
                        <div className="flex items-center gap-2">
                            {llmConnected === true && (
                                <span className="flex items-center gap-1 text-[10px] font-mono text-green-400">
                                    <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse"></span>Connected
                                </span>
                            )}
                            {llmConnected === false && (
                                <span className="flex items-center gap-1 text-[10px] font-mono text-red-400">
                                    <span className="w-2 h-2 rounded-full bg-red-400"></span>Offline
                                </span>
                            )}
                            <button onClick={saveLlmConfig}
                                className="px-3 py-1 bg-primary/20 hover:bg-primary/30 text-primary text-xs font-bold rounded transition">
                                Save Config
                            </button>
                        </div>
                    </div>

                    <div className="grid grid-cols-2 gap-4 mb-4">
                        {/* Provider */}
                        <div>
                            <label className="text-[10px] text-text-muted uppercase block mb-1.5">Provider</label>
                            <select
                                value={llmConfig.provider}
                                onChange={e => {
                                    const p = e.target.value;
                                    setLlmConfig(prev => ({ ...prev, provider: p }));
                                    setModels([]);
                                    setLlmConnected(null);
                                }}
                                className="w-full bg-onyx-black border border-border-dark rounded px-3 py-2 text-sm text-white font-mono focus:border-primary focus:outline-none transition"
                            >
                                <option value="ollama">Ollama</option>
                                <option value="lmstudio">LM Studio</option>
                            </select>
                        </div>

                        {/* URL */}
                        <div>
                            <label className="text-[10px] text-text-muted uppercase block mb-1.5">
                                {llmConfig.provider === "lmstudio" ? "LM Studio URL" : "Ollama URL"}
                            </label>
                            <div className="flex gap-2">
                                <input
                                    type="text"
                                    value={activeUrl}
                                    onChange={e => setActiveUrl(e.target.value)}
                                    placeholder="http://10.0.0.30:11434"
                                    className="flex-1 bg-onyx-black border border-border-dark rounded px-3 py-2 text-sm text-white font-mono focus:border-primary focus:outline-none transition"
                                />
                                <button
                                    onClick={() => fetchModels(llmConfig.provider, activeUrl)}
                                    disabled={modelsFetching}
                                    className="px-3 py-1.5 bg-onyx-surface hover:bg-onyx-panel border border-border-dark text-text-secondary text-xs font-bold rounded transition flex items-center gap-1"
                                    title="Test connection & fetch models"
                                >
                                    <span className={`material-symbols-outlined text-[14px] ${modelsFetching ? "animate-spin" : ""}`}>
                                        {modelsFetching ? "progress_activity" : "sync"}
                                    </span>
                                    Test
                                </button>
                            </div>
                        </div>
                    </div>

                    <div className="grid grid-cols-3 gap-4">
                        {/* Model Dropdown */}
                        <div>
                            <label className="text-[10px] text-text-muted uppercase block mb-1.5">Model</label>
                            {models.length > 0 ? (
                                <select
                                    value={llmConfig.model}
                                    onChange={e => setLlmConfig(prev => ({ ...prev, model: e.target.value }))}
                                    className="w-full bg-onyx-black border border-border-dark rounded px-3 py-2 text-sm text-white font-mono focus:border-primary focus:outline-none transition"
                                >
                                    {models.map(m => <option key={m} value={m}>{m}</option>)}
                                    {!models.includes(llmConfig.model) && llmConfig.model && (
                                        <option value={llmConfig.model}>{llmConfig.model} (not found)</option>
                                    )}
                                </select>
                            ) : (
                                <input
                                    type="text"
                                    value={llmConfig.model}
                                    onChange={e => setLlmConfig(prev => ({ ...prev, model: e.target.value }))}
                                    placeholder="gemma3:27b"
                                    className="w-full bg-onyx-black border border-border-dark rounded px-3 py-2 text-sm text-white font-mono focus:border-primary focus:outline-none transition"
                                />
                            )}
                        </div>

                        {/* Context Size */}
                        <div>
                            <label className="text-[10px] text-text-muted uppercase block mb-1.5">Context Size</label>
                            <input
                                type="number"
                                value={llmConfig.context_size}
                                onChange={e => setLlmConfig(prev => ({ ...prev, context_size: parseInt(e.target.value) || 8192 }))}
                                min={1024}
                                max={131072}
                                step={1024}
                                className="w-full bg-onyx-black border border-border-dark rounded px-3 py-2 text-sm text-white font-mono focus:border-primary focus:outline-none transition"
                            />
                        </div>

                        {/* Temperature */}
                        <div>
                            <label className="text-[10px] text-text-muted uppercase block mb-1.5">
                                Temperature <span className="text-primary font-bold ml-1">{llmConfig.temperature.toFixed(2)}</span>
                            </label>
                            <input
                                type="range"
                                value={llmConfig.temperature}
                                onChange={e => setLlmConfig(prev => ({ ...prev, temperature: parseFloat(e.target.value) }))}
                                min={0}
                                max={1}
                                step={0.05}
                                className="w-full accent-primary mt-1"
                            />
                            <div className="flex justify-between text-[9px] text-text-muted font-mono mt-0.5">
                                <span>Precise</span>
                                <span>Creative</span>
                            </div>
                        </div>
                    </div>

                    {/* Available models list */}
                    {models.length > 0 && (
                        <div className="mt-4 pt-3 border-t border-border-dark">
                            <div className="text-[10px] text-text-muted uppercase mb-2">
                                Available Models ({models.length})
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                                {models.map(m => (
                                    <button key={m}
                                        onClick={() => setLlmConfig(prev => ({ ...prev, model: m }))}
                                        className={`px-2 py-0.5 text-[11px] font-mono rounded transition ${m === llmConfig.model
                                            ? "bg-primary/20 text-primary border border-primary/40"
                                            : "bg-onyx-surface text-text-muted hover:text-white border border-border-dark hover:border-primary/30"
                                            }`}
                                    >
                                        {m}
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                {/* Strategy Editor */}
                <div className="glass-card p-5">
                    <div className="flex justify-between items-center mb-4">
                        <h3 className="text-sm font-bold text-white flex items-center gap-2">
                            <span className="material-symbols-outlined text-primary text-[18px]">description</span>
                            Trading Strategy
                        </h3>
                        <button onClick={saveStrategy}
                            className="px-3 py-1 bg-primary/20 hover:bg-primary/30 text-primary text-xs font-bold rounded transition">
                            Save Strategy
                        </button>
                    </div>
                    <textarea value={strategy} onChange={e => setStrategy(e.target.value)}
                        className="code-editor min-h-[200px]"
                        placeholder="# My Trading Strategy&#10;&#10;Describe your strategy here..." />
                </div>

                {/* Risk Params Editor */}
                <div className="glass-card p-5">
                    <div className="flex justify-between items-center mb-4">
                        <h3 className="text-sm font-bold text-white flex items-center gap-2">
                            <span className="material-symbols-outlined text-primary text-[18px]">tune</span>
                            Risk Parameters
                        </h3>
                        <button onClick={saveRisk}
                            className="px-3 py-1 bg-primary/20 hover:bg-primary/30 text-primary text-xs font-bold rounded transition">
                            Save Params
                        </button>
                    </div>
                    <textarea value={riskParams} onChange={e => setRiskParams(e.target.value)}
                        className="code-editor min-h-[200px]"
                        placeholder='{ "max_position_pct": 0.05 }' />
                </div>
            </div>
        </SidebarLayout>
    );
};

// ***************************************************************
// DIAGNOSTICS PAGE  DB stats + collector health
// ***************************************************************

const DiagnosticsPage = () => {
    const [stats, setStats] = useState(null);
    const [loading, setLoading] = useState(true);

    const loadStats = async () => {
        setLoading(true);
        const res = await fetch("/api/dashboard/db-stats");
        setStats(await res.json());
        setLoading(false);
    };

    useEffect(() => { loadStats(); }, []);

    return (
        <SidebarLayout active="diagnostics">
            <div className="h-14 flex items-center justify-between px-6 border-b border-border-dark bg-onyx-panel shrink-0">
                <h2 className="text-white font-bold text-lg">Diagnostics</h2>
                <button onClick={loadStats} className="px-3 py-1.5 bg-primary/20 hover:bg-primary/30 text-primary text-xs font-bold rounded transition flex items-center gap-1.5">
                    <span className="material-symbols-outlined text-[14px]">refresh</span>
                    Refresh
                </button>
            </div>
            <div className="flex-1 overflow-y-auto p-6">
                {loading ? <Spinner /> : stats && (
                    <div className="space-y-6">
                        <div className="glass-card p-5">
                            <h3 className="text-sm font-bold text-white mb-4 flex items-center gap-2">
                                <span className="material-symbols-outlined text-primary text-[18px]">database</span>
                                Database Table Sizes
                            </h3>
                            <div className="grid grid-cols-3 gap-3">
                                {Object.entries(stats.counts || {}).map(([table, count]) => (
                                    <div key={table} className="glass-card p-3 text-center">
                                        <div className={`text-2xl font-bold font-mono mb-1 ${count > 0 ? "text-green-400" : count < 0 ? "text-red-400" : "text-text-muted"}`}>
                                            {count >= 0 ? count.toLocaleString() : "N/A"}
                                        </div>
                                        <div className="text-[10px] text-text-muted uppercase">{table.replace(/_/g, " ")}</div>
                                        <div className={`text-[10px] mt-1 font-mono ${count > 0 ? "text-green-400" : "text-text-muted"}`}>
                                            {count > 0 ? "-- Active" : count < 0 ? "-- Missing" : "-- Empty"}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </SidebarLayout>
    );
};

// ***************************************************************
// APP  Root router
// ***************************************************************

const App = () => {
    const terminalData = useTerminalData();
    const { loading, error } = terminalData;

    if (loading) return (
        <div className="h-screen w-screen bg-onyx-black flex flex-col items-center justify-center">
            <span className="material-symbols-outlined text-primary animate-spin text-5xl mb-4">progress_activity</span>
            <h2 className="text-primary font-mono text-xl mb-2">Initializing Lazy Bot|</h2>
            <p className="text-text-muted text-xs font-mono">Loading terminal data</p>
        </div>
    );

    if (error) return (
        <div className="h-screen w-screen bg-onyx-black flex flex-col items-center justify-center">
            <span className="material-symbols-outlined text-red-400 text-5xl mb-4">error</span>
            <h2 className="text-red-400 font-mono text-lg mb-2">Connection Error</h2>
            <p className="text-text-muted text-xs font-mono">{error}</p>
        </div>
    );

    return (
        <HashRouter>
            <Routes>
                <Route path="/" element={<WatchlistPage {...terminalData} />} />
                <Route path="/analysis/:ticker" element={<AnalysisPage {...terminalData} />} />
                <Route path="/data/:ticker" element={<DataExplorerPage />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="/diagnostics" element={<DiagnosticsPage />} />
            </Routes>
        </HashRouter>
    );
};

const root = createRoot(document.getElementById("root"));
root.render(<App />);

