# Lazy Trading Bot

An autonomous stock trading bot that combines data collection, quant analysis, and LLM-powered decision-making into a single pipeline. Originally merged from separate Trading Terminal, Reddit Scraper, and YouTube-News-Extractor repos — the idea is to experiment with building AI-powered components and wiring them together into a cohesive system.

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Lazy Trading Bot                             │
│                      (Python / FastAPI / DuckDB)                     │
│                       http://localhost:8000                           │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │  Discovery    │  │  Collection  │  │   Trading    │               │
│  │  (YouTube,    │  │  (yfinance,  │  │  (TradingAgent│               │
│  │   Reddit,     │  │   News, SEC  │  │   + execution)│               │
│  │   RSS)        │  │   13F, etc.) │  │              │               │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘               │
│         │                 │                  │                        │
│         ▼                 ▼                  ▼                        │
│  ┌─────────────────────────────────────────────────┐                 │
│  │              LLM Service (llm_service.py)        │                │
│  │  • Request queue (serialized via semaphore)      │                │
│  │  • Dynamic timeouts based on model size          │                │
│  │  • JSON repair + retry logic                     │                │
│  │  • Conversation tracking per LLM call            │                │
│  └──────────────────────┬──────────────────────────┘                 │
│                         │ ALL LLM calls go through Prism             │
└─────────────────────────┼────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Prism — AI Gateway                                 │
│                  (Node.js / Express / MongoDB)                        │
│                    http://localhost:3020                              │
│                                                                      │
│  • Routes requests to LM Studio / Ollama / OpenAI / Anthropic / etc.│
│  • Logs EVERY request to MongoDB (tokens, cost, latency, model)      │
│  • Stores full conversations (system prompt + user context + reply)   │
│  • WebSocket streaming support                                       │
│  • Admin API for analytics, stats, and model management              │
│                                                                      │
│  MongoDB Collections:                                                │
│    requests      — per-request metadata (tokens, timing, success)    │
│    conversations — full message history per LLM interaction          │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                   Retina — Admin Dashboard                            │
│                     (Next.js / React)                                 │
│                    http://localhost:3000                              │
│                                                                      │
│  • Chat interface for testing models through Prism                   │
│  • Request log viewer with filtering and detail views                │
│  • Conversation browser — see exact prompts + LLM responses          │
│  • Live activity feed — watch trading bot conversations in real-time │
│  • Per-model / per-project cost and token analytics                  │
│  • LM Studio model management (load/unload models remotely)          │
└──────────────────────────────────────────────────────────────────────┘
```

## How The Projects Connect

| Project | Repo | Role | Port |
|---------|------|------|------|
| **Lazy Trading Bot** | `Lazy-Trading-Bot/` | Data collection, quant analysis, LLM-driven trading decisions | `8000` |
| **Prism** | `prism/` | AI Gateway — proxies all LLM calls, logs to MongoDB | `3020` |
| **Retina** | `retina/` | Admin dashboard — browse conversations, request logs, analytics | `3000` |

### Data Flow

1. **Lazy Trading Bot** sends all LLM requests to **Prism** via `POST /chat?stream=false`
2. **Prism** forwards the request to the configured LLM provider (Ollama, LM Studio, OpenAI, etc.)
3. **Prism** logs the request metadata (tokens, timing, cost) to the `requests` MongoDB collection
4. **Prism** auto-creates conversations via `conversationMeta` in the chat payload
5. **Retina** reads from Prism's admin API to display dashboards, conversation history, and analytics
6. At the end of each pipeline cycle, the bot posts a **workflow** to `POST /workflows` so the full run appears as a visual graph in Retna

### Why This Matters

- **Auditing**: Every LLM call the trading bot makes is stored in MongoDB with full prompt/response text. You can review exactly what context the LLM received and what it decided.
- **Cost tracking**: Prism tracks token counts and estimated cost per request, broken down by model and project.
- **Model comparison**: Request logs include latency, tokens/sec, and success rate per model — useful for choosing between `olmo-3`, `nemotron-3-nano`, `qwen3.5`, etc.
- **Debugging**: When the bot makes a bad trade, you can pull up the exact conversation in Retina to see what data it had (or was missing).

## Pipeline Stages

The bot runs an autonomous loop with these stages:

### 1. Discovery
Finds new stock tickers from multiple sources:
- **YouTube** — LLM extracts tickers + trading data from video transcripts
- **Reddit** — Scrapes r/stocks, r/wallstreetbets, r/investing for ticker mentions
- **RSS News** — Monitors financial news feeds
- **SEC 13F** — Tracks hedge fund holdings from quarterly filings
- **Congress** — Monitors congressional stock trades

### 2. Collection (Pipeline Service)
For each discovered ticker, collects 14 data types in parallel:
- Price history, fundamentals, financials, balance sheet, cash flows
- Technical indicators (SMA, RSI, MACD, Bollinger Bands)
- Analyst recommendations, insider transactions, earnings history
- News articles (full text), YouTube transcripts
- Industry peers (LLM-powered)

### 3. Analysis (Zero LLM — Pure Python)
- **QuantSignalEngine** — computes Sharpe, Sortino, Kelly fraction, VaR, max drawdown, trend template score, relative strength rating
- **DataDistiller** — generates plain-English summaries of chart patterns, fundamentals, and risk metrics
- **TickerDossier** — stored in DuckDB with conviction score and signal flags

### 4. Trading (LLM-Powered)
- **TradingAgent** — multi-turn LLM agent with access to research tools
- Context includes: price data, quant signals, distilled analysis, RAG-retrieved market intelligence, portfolio state
- Output: BUY/SELL/HOLD decision with confidence, rationale, and risk level
- Post-LLM guardrails override bad decisions (e.g., BUY against SELL verdict)

### 5. Execution
- **PaperTrader** — simulated execution with realistic fills
- Position tracking, P&L calculation, stop-loss management

### 6. RAG (Retrieval-Augmented Generation)
- Embeds YouTube transcripts, Reddit posts, news articles, and past trade decisions into DuckDB vectors
- Retrieves relevant context at trading time via cosine similarity
- Pre-computes query vectors during embedding phase to avoid VRAM conflicts

## Prerequisites

- **Python 3.12+**
- **TA-Lib C library** (auto-installed by `run.sh`)
- **Prism AI Gateway** running and accessible (default: `http://localhost:3020`)
- **LM Studio** or **Ollama** running with a loaded model (Prism routes to these)
- **MongoDB** (used by Prism for conversation/request logging)

## Setup

### 1. Start Prism (AI Gateway)

```bash
cd ../prism
npm install
cp secrets.example.js secrets.js   # Edit with your MongoDB URI, API keys, etc.
npm run dev                        # Starts on port 3020
```

### 2. Start Retina (Optional — Admin Dashboard)

```bash
cd ../retina
npm install
cp secrets.example.js secrets.js   # Set PRISM_URL and secrets
npm run dev                        # Starts on port 3000
```

### 3. Start Lazy Trading Bot

```bash
bash run.sh
```

The `run.sh` script handles everything:
- Checks for Python 3.12+
- Creates and activates a virtual environment
- Installs TA-Lib C library if missing
- Installs Python dependencies
- Pre-pulls the embedding model for RAG
- Starts the FastAPI server on port 8000

### 4. Configure LLM Connection

Edit `app/user_config/llm_config.json`:

```json
{
  "prism_url": "http://10.0.0.30:3020",
  "prism_secret": "banana",
  "prism_project": "lazy-trading-bot",
  "ollama_url": "http://10.0.0.30:11434",
  "model": "nemotron-3-nano:latest",
  "context_size": 8192,
  "temperature": 0.3
}
```

| Setting | Description |
|---------|-------------|
| `prism_url` | URL of the Prism AI Gateway (all LLM calls route through here) |
| `prism_secret` | Must match `GATEWAY_SECRET` in Prism's `secrets.js` |
| `prism_project` | Project name for grouping requests in Prism (default: `lazy-trading-bot`) |
| `ollama_url` | Direct Ollama URL — used **only** for model warm-up and VRAM estimation |
| `model` | LLM model name (e.g. `nemotron-3-nano:latest`, `olmo-3:latest`) |
| `context_size` | Max context window in tokens |
| `temperature` | LLM temperature for general calls (trading uses `trading_temperature`) |

## Project Structure

```
Lazy-Trading-Bot/
├── app/
│   ├── config.py              # Central config (Prism URL, model, RAG settings)
│   ├── database.py            # DuckDB schema and queries
│   ├── main.py                # FastAPI app + UI routes
│   ├── models/                # Pydantic models (TradeAction, TickerDossier, etc.)
│   ├── prompts/               # LLM prompt templates (portfolio_strategist.md, etc.)
│   ├── routers/               # API routers (pipeline, trading, portfolio, etc.)
│   ├── services/              # Core services (see below)
│   ├── static/                # Frontend assets (CSS, JS)
│   ├── templates/             # Jinja2 HTML templates
│   ├── user_config/           # Persistent LLM config (llm_config.json)
│   └── utils/                 # Logger, helpers
├── data/
│   ├── trading_bot.duckdb     # Main database (prices, fundamentals, decisions, embeddings)
│   ├── artifacts/             # Per-cycle pipeline artifacts (JSON snapshots)
│   ├── cache/                 # Response caches
│   └── reports/               # Generated health reports
├── plan/                      # Implementation plans and audits
├── tests/                     # Pytest test suite
├── run.sh                     # One-command launcher (setup + run)
├── server.py                  # Uvicorn entry point
└── requirements.txt           # Python dependencies
```

### Key Services

| Service | File | Description |
|---------|------|-------------|
| `LLMService` | `llm_service.py` | Routes all LLM calls through Prism (`/chat`), handles retries, JSON repair, conversation tracking |
| `WorkflowService` | `WorkflowService.py` | Posts pipeline workflows to Prism (`/workflows`) for Retna dashboard display |
| `PipelineService` | `pipeline_service.py` | Orchestrates 14-step data collection per ticker |
| `TradingAgent` | `trading_agent.py` | Multi-turn LLM agent — research tools → trading decision |
| `TradingPipelineService` | `trading_pipeline_service.py` | Builds context, gets LLM decision, applies guardrails, executes |
| `DeepAnalysisService` | `deep_analysis_service.py` | Quant scorecard + data distillation (zero LLM calls) |
| `DataDistiller` | `data_distiller.py` | Converts raw data to plain-English chart/fundamental/risk summaries |
| `QuantSignalEngine` | `quant_engine.py` | Pure-math quant scoring (Sharpe, Kelly, VaR, trend template) |
| `EmbeddingService` | `embedding_service.py` | Embeds text into DuckDB vectors for RAG |
| `RetrievalService` | `retrieval_service.py` | Cosine similarity search over embeddings for trading context |
| `TickerScanner` | `ticker_scanner.py` | LLM-powered ticker extraction from YouTube transcripts |
| `DiscoveryService` | `discovery_service.py` | Aggregates tickers from YouTube, Reddit, RSS, SEC, Congress |
| `PaperTrader` | `paper_trader.py` | Simulated trade execution with position tracking |
| `AutonomousLoop` | `autonomous_loop.py` | Scheduler that runs the full pipeline on configured intervals |

## Conversation Types in Prism

When reviewing conversations in Retina (`/admin/conversations`), you'll see these types from the trading bot:

| Title Pattern | What It Is | Example |
|---------------|-----------|---------|
| `AAPL — trading_decision_turn_0` | Trading agent analyzing a ticker | Full context (price, quant, analysis) → BUY/SELL/HOLD JSON |
| `Video Title — youtube_ticker_scan` | YouTube transcript → ticker extraction | Transcript text → extracted tickers + trading data JSON |
| `AAPL — peer_discovery` | Finding industry peers for a ticker | Ticker + company info → JSON array of 3 competitor tickers |
| `nemotron-3-nano:latest generation` | Model warm-up ping | "Say OK" → "OK" |

## Accessing the Data

### Trading Bot UI
- **Dashboard**: `http://localhost:8000` — portfolio overview, bot status, pipeline health
- **API docs**: `http://localhost:8000/docs` — full Swagger/OpenAPI documentation

### Prism Admin (via Retina)
- **Conversations**: `http://localhost:3000/admin/conversations` — browse all LLM conversations with full prompt/response text
- **Request Logs**: `http://localhost:3000/admin/requests` — per-request token counts, latency, success/failure
- **Live Activity**: `http://localhost:3000/admin/live` — real-time view of active conversations
- **Stats**: `http://localhost:3000/admin` — aggregate analytics by model, project, endpoint

### DuckDB (Direct)
The trading bot stores all collected data in `data/trading_bot.duckdb`. Key tables:
- `price_history`, `fundamentals`, `technicals`, `financial_history`
- `trade_decisions`, `trade_executions`, `portfolio_positions`
- `youtube_transcripts`, `youtube_trading_data`
- `embeddings` (RAG vector store)
- `ticker_dossiers`, `quant_scorecards`
- `watchlist`, `discovered_tickers`

### MongoDB (via Prism)
Prism stores LLM interaction data in MongoDB (database: `prism`):
- `requests` — one doc per LLM call (tokens, cost, latency, model, success/error)
- `conversations` — full message arrays (system prompt + user context + assistant response)
