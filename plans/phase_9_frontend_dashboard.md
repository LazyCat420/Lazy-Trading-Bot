# Phase 9: Frontend Dashboard

> Build a single-page dashboard for running analysis and viewing results.

## Overview

A lightweight HTML/CSS/JS dashboard served by FastAPI. No framework (React/Vue) — just vanilla JS for simplicity. The API endpoints already exist.

## Pages

### 1. Dashboard Home (`/`)

- **Watchlist panel**: Shows tickers from `watchlist.json` with last analysis status
- **Quick actions**: "Run Full Analysis", "Run Quick Analysis" buttons per ticker
- **Health check**: Shows LLM provider status (Ollama or LM Studio, which model, connection status)

### 2. Analysis Results (`/results/{ticker}`)

- **4 agent report cards**: Technical, Fundamental, Sentiment, Risk — each shows signal (bullish/bearish/neutral), confidence, and key highlights
- **Final decision banner**: BUY/SELL/HOLD with confidence percentage
- **Data freshness indicators**: When each data source was last updated

### 3. Strategy Editor (`/strategy`)

- **Live editor** for `strategy.md` (textarea with save button)
- **Risk params editor**: JSON editor for `risk_params.json`
- **Watchlist editor**: Add/remove tickers

### 4. Data Explorer (`/data/{ticker}`)

- **Price chart**: OHLCV with technical overlay (SMA lines, Bollinger Bands)
- **Fundamentals table**: Key metrics from latest snapshot
- **YouTube transcripts**: List of collected transcripts with expand/collapse
- **News articles**: Headline list with timestamps

## Design Aesthetics

- **Dark theme** with accent colors for signal types (green=bullish, red=bearish, amber=neutral)
- **Glassmorphism cards** for agent reports
- **Inter font** from Google Fonts
- **Micro-animations** on status changes, card reveals, signal indicators
- **Responsive**: Works on both desktop and mobile

## Tech Stack

- **Backend**: FastAPI (already built) serving static files
- **Frontend**: HTML + Vanilla CSS + Vanilla JS
- **Charts**: Lightweight charting library (Chart.js or similar via CDN)
- **No build step**: Just static files in `app/static/`

## API Endpoints (Already Built)

| Endpoint | Purpose |
|----------|---------|
| `POST /analyze/{ticker}` | Run full pipeline |
| `GET /health` | Check LLM + system status |
| `GET /strategy` | Read current strategy |
| `PUT /strategy` | Update strategy |
| `GET /watchlist` | Get ticker list |
| `PUT /watchlist` | Update ticker list |
| `GET /risk-params` | Get risk parameters |
| `PUT /risk-params` | Update risk parameters |

## Implementation Order

1. Static file serving in FastAPI
2. Dashboard home with watchlist + health
3. Analysis results page with agent cards
4. Strategy/config editor
5. Data explorer with charts
6. Polish (animations, responsive, error handling)
