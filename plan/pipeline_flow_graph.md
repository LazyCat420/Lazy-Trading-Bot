# Trading Bot Pipeline Flow Graph

## Full Pipeline: `AutonomousLoop.run_full_loop()`

```mermaid
graph TB
    subgraph INIT["🔧 Initialization"]
        UNLOAD["Unload All Models<br/>LLMService.unload_all_ollama_models()"]
        WARM["Pre-warm LLM Model<br/>verify_and_warm_ollama_model()<br/>keep_alive=2h"]
        UNLOAD --> WARM
    end

    subgraph P1["Phase 1: Discovery"]
        REDDIT["Reddit Scanner<br/>r/wallstreetbets, r/stocks, etc."]
        YOUTUBE["YouTube Scanner<br/>Search + transcript extraction"]
        RSS["RSS News Scanner<br/>Financial news feeds"]
        SEC13F["SEC 13F Scanner<br/>Institutional filings"]
        CONGRESS["Congress Scanner<br/>Congressional trades"]
        REDDIT --> DISC_MERGE["Merge & Score<br/>discovered_tickers + ticker_scores"]
        YOUTUBE --> DISC_MERGE
        RSS --> DISC_MERGE
        SEC13F --> DISC_MERGE
        CONGRESS --> DISC_MERGE
    end

    subgraph P2["Phase 2: Import"]
        IMPORT["WatchlistManager.import_from_discovery()<br/>min_score=3.0, max=10"]
        DISC_MERGE --> IMPORT
        IMPORT --> WATCHLIST["watchlist table<br/>status=active"]
    end

    subgraph P3["Phase 3: Data Collection"]
        direction LR
        COLL_START["PipelineService.run(ticker, mode='data')"]
        COLL_START --> PRICE["yfinance: price_history<br/>90 days OHLCV"]
        COLL_START --> TECH["pandas_ta: technicals<br/>50+ indicators"]
        COLL_START --> FUND["yfinance: fundamentals<br/>PE, margins, MCap"]
        COLL_START --> FIN_HIST["yfinance: financial_history<br/>5yr P&L"]
        COLL_START --> BS["yfinance: balance_sheet<br/>5yr assets/liabilities"]
        COLL_START --> CF["yfinance: cash_flows<br/>5yr FCF/buybacks"]
        COLL_START --> RISK["compute: risk_metrics<br/>Sharpe, VaR, drawdown"]
        COLL_START --> NEWS["yfinance: news_articles<br/>10 articles"]
        COLL_START --> YT_COLLECT["YouTube: youtube_transcripts<br/>5+ transcripts"]
        COLL_START --> ANALYST["yfinance: analyst_data<br/>consensus ratings"]
        COLL_START --> INSIDER["yfinance: insider_activity<br/>insider trades"]
        COLL_START --> EARNINGS["yfinance: earnings_calendar<br/>next earnings date"]
    end

    subgraph P4["Phase 4: RAG Embedding"]
        EMBED_SVC["EmbeddingService"]
        EMBED_MODEL["Load nomic-embed-text<br/>via Ollama /api/embed"]
        EMBED_YT["Embed YouTube transcripts"]
        EMBED_NEWS["Embed News articles"]
        EMBED_REDDIT["Embed Reddit posts"]
        EMBED_DECISIONS["Embed Trade decisions"]
        PRECOMPUTE["Pre-compute query vectors<br/>for each active ticker"]
        EMBED_SVC --> EMBED_MODEL
        EMBED_MODEL --> EMBED_YT
        EMBED_MODEL --> EMBED_NEWS
        EMBED_MODEL --> EMBED_REDDIT
        EMBED_MODEL --> EMBED_DECISIONS
        EMBED_YT --> PRECOMPUTE
        EMBED_NEWS --> PRECOMPUTE
        EMBED_REDDIT --> PRECOMPUTE
        EMBED_DECISIONS --> PRECOMPUTE
    end

    subgraph P5["Phase 5: Deep Analysis"]
        DA_SVC["DeepAnalysisService.analyze_batch()"]
        QUANT["Layer 1: Quant Scorecard<br/>z-scores, Sharpe, VaR, Kelly"]
        DISTILL_NEWS["Layer 2a: Distill News<br/>LLM summarize news articles"]
        DISTILL_YT["Layer 2b: Distill YouTube<br/>LLM summarize transcripts"]
        DISTILL_SM["Layer 2c: Distill Smart Money<br/>SEC 13F + Congress"]
        DISTILL_RED["Layer 2d: Distill Reddit<br/>Sentiment analysis"]
        CROSS["Layer 3: Cross-Signal Summary<br/>LLM synthesize all signals"]
        DOSSIER["Layer 4: Ticker Dossier<br/>executive_summary, bull/bear,<br/>conviction_score"]
        DA_SVC --> QUANT
        QUANT --> DISTILL_NEWS
        QUANT --> DISTILL_YT
        QUANT --> DISTILL_SM
        QUANT --> DISTILL_RED
        DISTILL_NEWS --> CROSS
        DISTILL_YT --> CROSS
        DISTILL_SM --> CROSS
        DISTILL_RED --> CROSS
        CROSS --> DOSSIER
    end

    subgraph P6["Phase 6: Trading Decisions"]
        TP_SVC["TradingPipelineService"]
        BUILD_CTX["_build_context()<br/>Reads: dossier, technicals,<br/>fundamentals, risk, portfolio,<br/>RAG context, delta_since_last"]
        LLM_CALL["LLM Call via Prism<br/>→ JSON TradeAction"]
        PARSE["TradeActionParser<br/>BUY/SELL/HOLD + confidence"]
        VALIDATE["Validate Trade<br/>cash check, position limits"]
        EXECUTE["ExecutionService<br/>paper_trader.place_order()"]
        LOG_DECISION["Log to trade_decisions table"]
        TP_SVC --> BUILD_CTX
        BUILD_CTX --> LLM_CALL
        LLM_CALL --> PARSE
        PARSE --> VALIDATE
        VALIDATE --> EXECUTE
        VALIDATE --> LOG_DECISION
    end

    subgraph P7["Phase 7: Post-Processing"]
        HEALTH["Health Report<br/>pipeline_health.py"]
        EVOLVE["Prompt Evolution<br/>PromptEvolver.evolve()"]
        AUDIT["Cross-Bot Audit<br/>CrossBotAuditor"]
        IMPROVE["Improvement Feed<br/>ImprovementFeed"]
    end

    INIT --> P1
    P1 --> P2
    P2 --> P3
    P3 --> P4
    P4 --> P5
    P5 --> P6
    P6 --> P7

    style INIT fill:#1a1a2e,stroke:#e94560,color:#fff
    style P1 fill:#16213e,stroke:#0f3460,color:#fff
    style P2 fill:#1a1a2e,stroke:#533483,color:#fff
    style P3 fill:#16213e,stroke:#0f3460,color:#fff
    style P4 fill:#1a1a2e,stroke:#e94560,color:#fff
    style P5 fill:#16213e,stroke:#0f3460,color:#fff
    style P6 fill:#1a1a2e,stroke:#e94560,color:#fff
    style P7 fill:#16213e,stroke:#533483,color:#fff
```

## Data Flow Per Phase

| Phase | Input Tables | Output Tables | LLM Required |
|-------|-------------|---------------|:---:|
| Discovery | — (external APIs) | `discovered_tickers`, `ticker_scores`, `youtube_transcripts` | ✅ (AgenticExtractor) |
| Import | `ticker_scores` | `watchlist` | ❌ |
| Collection | `watchlist` | `price_history`, `technicals`, `fundamentals`, `financial_history`, `balance_sheet`, `cash_flows`, `risk_metrics`, `news_articles`, `analyst_data`, `insider_activity`, `earnings_calendar` | ❌ |
| Embedding | all content tables | `embeddings` (vector store) | ❌ (embed model only) |
| Deep Analysis | all data tables | `quant_scorecards`, `ticker_dossiers` | ✅ (distillation + synthesis) |
| Trading | `ticker_dossiers`, `technicals`, `fundamentals`, `risk_metrics`, `embeddings`, `trade_decisions` | `trade_decisions`, `orders`, `positions` | ✅ (trade decision) |

## Known Issues

1. **Dual Model Loading** — ✅ Fixed: `unload_all_ollama_models()` before warm-up
2. **LLM stops at summaries** — Deep Analysis Layer 2 (distillation) may not feed into Layer 3/4 properly
3. **Discovery runs during test** — Need to skip discovery/collection when using test DB (data pre-seeded)
