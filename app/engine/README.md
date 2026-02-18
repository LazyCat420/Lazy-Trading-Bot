# Engine Module — 4-Layer Analysis Funnel

This folder contains the core analysis pipeline that transforms raw Phase-1 data into decision-ready ticker dossiers.

## Architecture

```
Layer 1: quant_signals.py      → QuantScorecard (pure math)
Layer 2: question_generator.py → 5 Follow-up Questions (LLM)
Layer 3: rag_engine.py         → 5 QAPairs (BM25 + LLM)
Layer 4: dossier_synthesizer.py → TickerDossier (LLM synthesis)
```

## Data Flow

1. **Layer 1** reads DuckDB tables (`price_history`, `risk_metrics`, `technicals`, `earnings_calendar`, `insider_activity`) and computes 14 quantitative metrics plus anomaly flags
2. **Layer 2** sends the scorecard to the LLM, which generates 5 targeted questions
3. **Layer 3** routes each question to the appropriate data source, searches via BM25, and extracts answers via LLM
4. **Layer 4** compresses everything into an executive summary, bull/bear cases, catalysts, and a conviction score

## Dependencies

- `rank-bm25` — BM25 keyword search (Layer 3)
- `numpy`, `scipy` — math computations (Layer 1)
- LLM backend (Ollama/LM Studio) — Layers 2, 3, 4

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/analysis/deep/{ticker}` | Full 4-layer analysis |
| POST | `/api/analysis/deep-batch` | Batch analysis for watchlist |
| GET | `/api/dossiers/{ticker}` | Retrieve stored dossier |
| GET | `/api/scorecards/{ticker}` | Retrieve quant scorecard |
